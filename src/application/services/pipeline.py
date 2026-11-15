import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid7

import httpx
from fastapi.encoders import jsonable_encoder
from loguru import logger

from src.application.exception import PathConflictError
from src.application.schemas import caption_token
from src.application.services.task import TaskService
from src.application.services.temp import temp_path
from src.config import settings
from src.infrastructure.background import BackgroundRuntime, Gate, LiveTask
from src.infrastructure.database.models import FileDB, TaskDB
from src.infrastructure.database.repositories import (
    FileRepository,
    FolderRepository,
    StorageRepository,
    TaskRepository,
)
from src.infrastructure.database.session import session_factory
from src.infrastructure.enums import MediaKind, TaskStage, TaskStatus
from src.infrastructure.integrations import (
    ScannerUnavailableError,
    TelegramFloodWaitError,
    TelegramUnavailableError,
    UnsafeUrlError,
)
from src.infrastructure.integrations.web import open_url
from src.infrastructure.media import photo_fits, probe, thumbnail, to_jpeg
from src.utilities.errors import TCloudError
from src.utilities.parsers import split_file

TELEGRAM_PHOTO_MIMES = ("image/jpeg", "image/png")


def describe(error: BaseException) -> dict:
    if isinstance(error, TCloudError):
        return error.to_dict()
    if isinstance(error, asyncio.CancelledError):
        return {"code": "interrupted", "message": "task was interrupted"}
    return {"code": "internal", "message": "internal error"}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class UploadPipeline:
    def __init__(self, runtime: BackgroundRuntime) -> None:
        self._runtime = runtime

    def start(self, task: TaskDB) -> None:
        self._runtime.live[task.id] = LiveTask(
            stage=TaskStage.RECEIVING,
            bytes_total=task.bytes_total,
            callback_url=task.callback_url,
        )
        self.hook(task.id)

    async def run(
        self,
        task: TaskDB,
        path: Path,
        receive: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        extra: list[Path] = []
        try:
            if receive:
                await receive()
            await self._process(task, path, extra)
        except BaseException as error:
            await self.fail(task, error)
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            for item in (path, *extra):
                item.unlink(missing_ok=True)

    @asynccontextmanager
    async def slot(self, task: TaskDB, gate: Gate):
        live = self._runtime.live[task.id]
        live.gate = gate
        self.hook(task.id)
        try:
            async with gate.slot(task.id):
                live.gate = None
                live.started = time.monotonic()
                yield
        finally:
            live.gate = None

    async def _retry(
        self,
        task: TaskDB,
        call: Callable[[], Awaitable],
        retryable: type[BaseException],
    ):
        for attempt in range(settings.upload.RETRIES + 1):
            try:
                return await call()
            except retryable as error:
                if attempt == settings.upload.RETRIES:
                    raise
                delay = settings.upload.RETRY_DELAY * 2**attempt
                logger.warning(
                    "Task {}: {!r}, retry {} in {:.1f} s",
                    task.id,
                    error,
                    attempt + 1,
                    delay,
                )
                await self._wait(task, "retry", delay)

    async def _wait(self, task: TaskDB, reason: str, seconds: float) -> None:
        live = self._runtime.live.get(task.id)
        if live:
            live.waiting_reason, live.waiting_until = reason, time.time() + seconds
            self.hook(task.id)
        try:
            await asyncio.sleep(seconds)
        finally:
            if live:
                live.waiting_reason = live.waiting_until = None

    async def _send_to_telegram(self, task: TaskDB, **kwargs):
        while True:
            pause = self._runtime.telegram_resume_at - time.monotonic()
            if pause > 0:
                await self._wait(task, "flood_wait", pause)
                continue
            try:
                return await self._retry(
                    task,
                    lambda: self._runtime.telegram.send(**kwargs),
                    TelegramUnavailableError,
                )
            except TelegramFloodWaitError as error:
                if error.seconds > settings.telegram.FLOOD_MAX_WAIT:
                    raise
                self._runtime.telegram_resume_at = max(
                    self._runtime.telegram_resume_at, time.monotonic() + error.seconds
                )
                logger.warning(
                    "Telegram flood wait {} s: all uploads paused", error.seconds
                )

    async def _existing(
        self, storage_id: UUID, folder: str, name: str, on_conflict: str
    ) -> tuple[FileDB | None, int]:
        async with session_factory() as session:
            storage = await StorageRepository(session).get_by_id(storage_id)
            existing = await FileRepository(session).get_by_path(
                storage_id, folder, name
            )
        if existing and on_conflict in ("error", "keep_both"):
            raise PathConflictError(f"{name} already exists")
        return existing, storage.tg_chat_id

    async def _process(self, task: TaskDB, path: Path, extra: list[Path]) -> None:
        params = task.params
        storage_id, folder, name = split_file(task.path)

        await self.stage(task, TaskStage.SCANNING, None)
        async with self.slot(task, self._runtime.scans):
            media = await asyncio.to_thread(probe, path, name)
            sha256 = await asyncio.to_thread(sha256_of, path)
            await self._retry(
                task, lambda: self._runtime.scanner.scan(path), ScannerUnavailableError
            )

            as_file = params["send_as"] == "file"
            warnings: list[str] = []
            send_path, thumb = path, None
            if not as_file and media.kind == MediaKind.PHOTO:
                if not photo_fits(media, path.stat().st_size):
                    as_file = True
                    warnings.append(
                        "photo does not fit Telegram photo limits (10 MB, sides), sent as file"
                    )
                elif media.mime not in TELEGRAM_PHOTO_MIMES:
                    send_path = temp_path("photo.jpg")
                    extra.append(send_path)
                    await asyncio.to_thread(to_jpeg, path, send_path)
            if (
                not as_file
                and media.kind == MediaKind.VIDEO
                and media.mime != "video/mp4"
            ):
                warnings.append(
                    f"{media.mime} may be shown as a file instead of a player in Telegram"
                )
            if media.kind == MediaKind.VIDEO:
                thumb = temp_path("thumb.jpg")
                extra.append(thumb)
                if not await asyncio.to_thread(thumbnail, path, thumb, media.duration):
                    thumb = None

        existing, chat_id = await self._existing(
            storage_id, folder, name, params["on_conflict"]
        )
        if existing and params["on_conflict"] == "skip":
            await self.finish(task, existing.id, ["skipped: file already exists"])
            return
        file_id = existing.id if existing else uuid7()

        await self.stage(task, TaskStage.UPLOADING_TG, send_path.stat().st_size)
        async with self.slot(task, self._runtime.uploads):
            sent = await self._send_to_telegram(
                task,
                chat_id=chat_id,
                path=send_path,
                name=name,
                caption=caption_token(file_id),
                media=media,
                as_file=as_file,
                thumb=thumb,
                progress=lambda done, total: self.progress(task.id, done, total),
            )
        compressed = media.kind == MediaKind.PHOTO and not as_file
        values = dict(
            folder_path=folder,
            name=name,
            size=sent.size,
            mime=(sent.mime or "image/jpeg") if compressed else media.mime,
            kind=media.kind,
            sent_as="file" if as_file else "media",
            width=sent.width or media.width,
            height=sent.height or media.height,
            duration=media.duration,
            sha256=sha256,
            tg_message_id=sent.message_id,
        )
        try:
            async with session_factory() as session:
                await FolderRepository(session).ensure(storage_id, folder)
                if existing:
                    await FileRepository(session).update(file_id, values)
                else:
                    await FileRepository(session).create(
                        FileDB(id=file_id, storage_id=storage_id, **values)
                    )
                await session.commit()
        except BaseException:
            await self._runtime.telegram.delete(chat_id, [sent.message_id])
            raise
        if existing:
            await self._runtime.telegram.delete(chat_id, [existing.tg_message_id])
        await self.finish(task, file_id, warnings)

    async def _save(self, task_id: UUID, **values) -> None:
        async with session_factory() as session:
            await TaskRepository(session).set_values(task_id, values)
            await session.commit()

    async def stage(self, task: TaskDB, stage: TaskStage, total: int | None) -> None:
        live = self._runtime.live[task.id]
        live.stage, live.bytes_done, live.bytes_total, live.started = (
            stage,
            0,
            total,
            time.monotonic(),
        )
        await self._save(task.id, stage=stage, bytes_done=0, bytes_total=total)
        self.hook(task.id)

    def progress(self, task_id: UUID, done: int, total: int | None = None) -> None:
        live = self._runtime.live.get(task_id)
        if not live:
            return
        live.bytes_done = done
        if total:
            live.bytes_total = total
        if time.monotonic() - live.hook_at >= settings.upload.WEBHOOK_INTERVAL:
            self.hook(task_id)

    async def finish(self, task: TaskDB, file_id: UUID, warnings: list[str]) -> None:
        live = self._runtime.live.pop(task.id, None)
        done = (live.bytes_total or live.bytes_done) if live else 0
        await self._save(
            task.id,
            status=TaskStatus.DONE,
            stage=TaskStage.DONE,
            file_id=file_id,
            warnings=warnings,
            bytes_done=done,
        )
        logger.info("Task {} done: {}", task.id, task.path)
        self.hook(task.id, task.callback_url)

    async def fail(self, task: TaskDB, error: BaseException) -> None:
        details = describe(error)
        if details["code"] == "internal":
            logger.opt(exception=error).error("Task {} failed", task.id)
        else:
            logger.warning("Task {} failed: {}", task.id, details["message"])
        live = self._runtime.live.pop(task.id, None)
        try:
            await asyncio.shield(
                self._save(
                    task.id,
                    status=TaskStatus.FAILED,
                    error=details,
                    bytes_done=live.bytes_done if live else 0,
                )
            )
        except Exception:
            logger.exception("Cannot save failed task {}", task.id)
        self.hook(task.id, task.callback_url)

    def hook(self, task_id: UUID, url: str | None = None) -> None:
        live = self._runtime.live.get(task_id)
        url = url or (live.callback_url if live else None)
        if not url:
            return
        if live:
            live.hook_at = time.monotonic()
        self._runtime.spawn(self._send_hook(task_id, url))

    async def _send_hook(self, task_id: UUID, url: str) -> None:
        async with session_factory() as session:
            view = await TaskService(session, self._runtime).get(task_id)
        body = json.dumps(jsonable_encoder(view)).encode()
        signature = hmac.new(
            settings.app.API_KEY.get_secret_value().encode(), body, hashlib.sha256
        ).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-Tcloud-Signature": f"sha256={signature}",
        }
        for delay in (1, 2, 4, None):
            try:
                async with open_url(
                    url, "POST", headers=headers, content=body
                ) as response:
                    if response.status_code < 500:
                        return
            except UnsafeUrlError as error:
                logger.warning("Webhook {} rejected: {}", url, error)
                return
            except httpx.HTTPError as error:
                logger.warning("Webhook {} failed: {!r}", url, error)
            if delay:
                await asyncio.sleep(delay)
