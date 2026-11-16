import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
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
from src.infrastructure.background import WorkerRuntime, enqueue
from src.infrastructure.database.models import FileDB
from src.infrastructure.database.repositories import (
    FileRepository,
    FolderRepository,
    StorageRepository,
)
from src.infrastructure.enums import (
    MediaKind,
    TaskSource,
    TaskStage,
    TaskStatus,
    UploadQueue,
)
from src.infrastructure.integrations import (
    ScannerUnavailableError,
    TelegramFloodWaitError,
    TelegramUnavailableError,
    UnsafeUrlError,
)
from src.infrastructure.integrations.web import download, open_url
from src.infrastructure.media import Media, photo_fits, probe, thumbnail, to_jpeg
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


def unlink(*paths: str | None) -> None:
    for item in paths:
        if item:
            Path(item).unlink(missing_ok=True)


class UploadPipeline:
    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime
        self._store = runtime.store
        self._progress_at: dict[UUID, float] = {}
        self._progress_save: dict[UUID, asyncio.Task] = {}

    async def _active(self, task_id: UUID) -> dict | None:
        task = await self._store.get(task_id)
        if not task or task["status"] in TaskStatus.finished():
            return None
        return task

    async def prepare(self, task_id: UUID) -> None:
        task = await self._active(task_id)
        if not task:
            return
        params = task["params"]
        extra: list[str] = []
        try:
            if params["source"] == TaskSource.URL:
                await self.stage(task, TaskStage.DOWNLOADING, None)
                await self._download(task)
            await self.stage(task, TaskStage.SCANNING, None)
            payload = await self._scan(task, Path(params["file"]), extra)
            await self.stage(
                task,
                TaskStage.UPLOADING_TG,
                Path(payload["send_path"]).stat().st_size,
                waiting={"reason": f"queue:{UploadQueue.TELEGRAM}"},
            )
            await enqueue(
                UploadQueue.TELEGRAM,
                task["priority_level"],
                task_id=str(task_id),
                payload=payload,
            )
        except BaseException as error:
            unlink(params["file"], *extra)
            await self.fail(task, error)
            if isinstance(error, asyncio.CancelledError):
                raise

    async def _download(self, task: dict) -> None:
        params = task["params"]
        reservation = self._runtime.disk.reserve(0)

        def progress(done: int, total: int | None) -> None:
            reservation.update(done, total)
            self.progress(task["id"], done, total)

        try:
            await download(
                params["url"], Path(params["file"]), params["limit"], progress
            )
        finally:
            reservation.release()

    async def _retry(
        self,
        task: dict,
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
                    task["id"],
                    error,
                    attempt + 1,
                    delay,
                )
                await self._wait(task, "retry", delay)

    async def _wait(self, task: dict, reason: str, seconds: float) -> None:
        await self._store.update(
            task["id"], waiting={"reason": reason, "until": time.time() + seconds}
        )
        self.hook(task)
        try:
            await asyncio.sleep(seconds)
        finally:
            await self._store.update(task["id"], waiting=None)

    async def _scan(self, task: dict, path: Path, extra: list[str]) -> dict:
        _, _, name = split_file(task["path"])
        media = await asyncio.to_thread(probe, path, name)
        sha256 = await asyncio.to_thread(sha256_of, path)
        await self._retry(
            task, lambda: self._runtime.scanner.scan(path), ScannerUnavailableError
        )
        as_file = task["params"]["send_as"] == "file"
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
                extra.append(str(send_path))
                await asyncio.to_thread(to_jpeg, path, send_path)
        if not as_file and media.kind == MediaKind.VIDEO and media.mime != "video/mp4":
            warnings.append(
                f"{media.mime} may be shown as a file instead of a player in Telegram"
            )
        if media.kind == MediaKind.VIDEO:
            thumb = temp_path("thumb.jpg")
            extra.append(str(thumb))
            if not await asyncio.to_thread(thumbnail, path, thumb, media.duration):
                thumb = None
        return {
            "media": asdict(media),
            "sha256": sha256,
            "as_file": as_file,
            "warnings": warnings,
            "send_path": str(send_path),
            "thumb": str(thumb) if thumb else None,
        }

    async def send(self, task_id: UUID, payload: dict) -> None:
        task = await self._active(task_id)
        if not task:
            unlink(payload["send_path"], payload["thumb"])
            return
        try:
            await self._store.update(
                task_id,
                status=TaskStatus.RUNNING,
                waiting=None,
                stage_started=time.time(),
            )
            await self._send(task, payload)
        except BaseException as error:
            await self.fail(task, error)
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            unlink(task["params"]["file"], payload["send_path"], payload["thumb"])

    async def _existing(
        self, storage_id: UUID, folder: str, name: str, on_conflict: str
    ) -> tuple[FileDB | None, int]:
        async with self._runtime.session_factory() as session:
            storage = await StorageRepository(session).get_by_id(storage_id)
            existing = await FileRepository(session).get_by_path(
                storage_id, folder, name
            )
        if existing and on_conflict in ("error", "keep_both"):
            raise PathConflictError(f"{name} already exists")
        return existing, storage.tg_chat_id

    async def _send_to_telegram(self, task: dict, **kwargs):
        telegram = await self._runtime.telegram()
        while True:
            pause = self._runtime.telegram_resume_at - time.monotonic()
            if pause > 0:
                await self._wait(task, "flood_wait", pause)
                continue
            try:
                return await self._retry(
                    task, lambda: telegram.send(**kwargs), TelegramUnavailableError
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

    async def _send(self, task: dict, payload: dict) -> None:
        on_conflict = task["params"]["on_conflict"]
        storage_id, folder, name = split_file(task["path"])
        existing, chat_id = await self._existing(storage_id, folder, name, on_conflict)
        if existing and on_conflict == "skip":
            await self.finish(task, existing.id, ["skipped: file already exists"])
            return
        file_id = existing.id if existing else uuid7()
        media = Media(**payload["media"])
        as_file = payload["as_file"]
        sent = await self._send_to_telegram(
            task,
            chat_id=chat_id,
            path=Path(payload["send_path"]),
            name=name,
            caption=caption_token(file_id),
            media=media,
            as_file=as_file,
            thumb=Path(payload["thumb"]) if payload["thumb"] else None,
            progress=lambda done, total: self.progress(task["id"], done, total),
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
            sha256=payload["sha256"],
            tg_message_id=sent.message_id,
        )
        telegram = await self._runtime.telegram()
        try:
            async with self._runtime.session_factory() as session:
                await FolderRepository(session).ensure(storage_id, folder)
                if existing:
                    await FileRepository(session).update(file_id, values)
                else:
                    await FileRepository(session).create(
                        FileDB(id=file_id, storage_id=storage_id, **values)
                    )
                await session.commit()
        except BaseException:
            await telegram.delete(chat_id, [sent.message_id])
            raise
        if existing:
            await telegram.delete(chat_id, [existing.tg_message_id])
        await self.finish(task, file_id, payload["warnings"])

    async def stage(
        self,
        task: dict,
        stage: TaskStage,
        total: int | None,
        waiting: dict | None = None,
    ) -> None:
        await self._flush_progress(task["id"])
        await self._store.update(
            task["id"],
            status=TaskStatus.RUNNING,
            stage=stage,
            bytes_done=0,
            bytes_total=total,
            waiting=waiting,
            stage_started=time.time(),
        )
        self.hook(task)

    def progress(self, task_id: UUID, done: int, total: int | None = None) -> None:
        now = time.monotonic()
        if now - self._progress_at.get(task_id, 0) < settings.upload.WEBHOOK_INTERVAL:
            return
        self._progress_at[task_id] = now
        values = {"bytes_done": done}
        if total:
            values["bytes_total"] = total
        self._progress_save[task_id] = self._runtime.spawn(
            self._save_progress(task_id, values)
        )

    async def _save_progress(self, task_id: UUID, values: dict) -> None:
        await self._store.update(task_id, **values)
        task = await self._store.get(task_id)
        if task:
            self.hook(task)

    async def _flush_progress(self, task_id: UUID) -> None:
        self._progress_at.pop(task_id, None)
        pending = self._progress_save.pop(task_id, None)
        if pending:
            await asyncio.gather(pending, return_exceptions=True)

    async def _close(self, task: dict, **values) -> None:
        await self._flush_progress(task["id"])
        await self._store.update(task["id"], waiting=None, **values)
        await self._store.deactivate(task["id"])
        await self._store.unlock_path(task["path"], task["id"])
        self.hook(task)

    async def finish(self, task: dict, file_id: UUID, warnings: list[str]) -> None:
        current = await self._store.get(task["id"]) or {}
        await self._close(
            task,
            status=TaskStatus.DONE,
            stage=TaskStage.DONE,
            file_id=file_id,
            warnings=warnings,
            bytes_done=current.get("bytes_total") or current.get("bytes_done") or 0,
        )
        logger.info("Task {} done: {}", task["id"], task["path"])

    async def fail(self, task: dict, error: BaseException) -> None:
        details = describe(error)
        if details["code"] == "internal":
            logger.opt(exception=error).error("Task {} failed", task["id"])
        else:
            logger.warning("Task {} failed: {}", task["id"], details["message"])
        try:
            await asyncio.shield(
                self._close(task, status=TaskStatus.FAILED, error=details)
            )
        except Exception:
            logger.exception("Cannot save failed task {}", task["id"])

    def hook(self, task: dict) -> None:
        url = task["params"].get("callback_url")
        if url:
            self._runtime.spawn(self._send_hook(UUID(str(task["id"])), url))

    async def _send_hook(self, task_id: UUID, url: str) -> None:
        async with self._runtime.session_factory() as session:
            view = await TaskService(session, self._store).get(task_id)
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
