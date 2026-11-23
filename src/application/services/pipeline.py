import asyncio
import base64
import hashlib
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid7

from loguru import logger

from src.application.exception import PathConflictError, TaskCancelledError
from src.application.schemas import SKIPPED, caption_token, task_warning
from src.application.services.archive import build_archive
from src.application.services.storage import root_settings
from src.application.services.temp import remove_task_files, temp_path
from src.application.services.webhook import send_hook
from src.config import settings
from src.infrastructure.background import WorkerRuntime, enqueue
from src.infrastructure.cache import TaskStore
from src.infrastructure.database.models import FileDB
from src.infrastructure.database.repositories import (
    FileRepository,
    FolderRepository,
    StorageRepository,
)
from src.infrastructure.enums import (
    MediaKind,
    TaskStage,
    TaskStatus,
    TaskStep,
    UploadQueue,
)
from src.infrastructure.integrations import (
    ScannerUnavailableError,
    SentFile,
    TelegramFloodWaitError,
    TelegramUnauthorizedError,
    TelegramUnavailableError,
    UnscannableFileError,
)
from src.infrastructure.integrations.web import download
from src.infrastructure.media import (
    Media,
    photo_fits,
    preview,
    probe,
    thumbnail,
    to_jpeg,
)
from src.infrastructure.security import cipher_size, encrypt_file, seal
from src.utilities.errors import TCloudError
from src.utilities.parsers import split_file

TELEGRAM_PHOTO_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png"}
DISK_SLOT_POLL = 1.0
CANCEL_POLL = 1.0
PREVIEW_KINDS = {MediaKind.PHOTO, MediaKind.ANIMATION, MediaKind.VIDEO}


def describe(error: BaseException) -> dict:
    if isinstance(error, TCloudError):
        return error.to_dict()
    if isinstance(error, asyncio.CancelledError):
        return {"code": "interrupted", "message": "task was interrupted"}
    return {"code": "internal", "message": "internal error"}


def sha256_of(path: Path, progress: Callable[[int], None]) -> str:
    digest = hashlib.sha256()
    done = 0
    with open(path, "rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
            done += len(chunk)
            progress(done)
    return digest.hexdigest()


class UploadPipeline:
    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime
        self._progress_at: dict[str, float] = {}
        self._progress_save: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def _store(self) -> TaskStore:
        return self._runtime.store

    async def run(
        self, queue: UploadQueue, task_id: UUID, generation: int, redelivered: bool
    ) -> None:
        if not await self._store.claim(task_id, queue, generation, redelivered):
            logger.info("Task {} skipped in {}: stale or cancelled", task_id, queue)
            return
        self._loop = asyncio.get_running_loop()
        task = await self._store.get(task_id)
        step = {
            UploadQueue.DOWNLOAD: self._download,
            UploadQueue.PREPARE: self._prepare,
            UploadQueue.TELEGRAM: self._send,
            UploadQueue.ARCHIVE: self._archive,
        }[queue]
        beat = self._runtime.spawn(self._beat(task_id))
        watch = self._runtime.spawn(self._watch(task["id"]))
        kind = {UploadQueue.TELEGRAM: "write", UploadQueue.ARCHIVE: "read"}.get(queue)
        activity = kind and self._runtime.spawn(self._activity(task["path"], kind))
        try:
            await step(task)
        except BaseException as error:
            if isinstance(error, TelegramUnauthorizedError):
                await self._runtime.forget_telegram()
            await self.fail(task, error)
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            beat.cancel()
            watch.cancel()
            if activity:
                activity.cancel()

    async def _beat(self, task_id: UUID) -> None:
        while True:
            await self._store.touch(task_id)
            await asyncio.sleep(settings.upload.HEARTBEAT_INTERVAL)

    async def _watch(self, task_id: str) -> None:
        while not await self._store.cancel_requested(task_id):
            await asyncio.sleep(CANCEL_POLL)
        self._cancelled.add(task_id)

    def _check_cancel(self, task_id: str) -> None:
        if task_id in self._cancelled:
            raise TaskCancelledError("task was cancelled")

    async def _activity(self, path: str, kind: str) -> None:
        storage_id = path.split("/")[1]
        while True:
            await self._store.mark_activity(storage_id, kind)
            await asyncio.sleep(1)

    async def _checkpoint(self, task: dict) -> None:
        current = await self._store.get(task["id"]) or {}
        if current.get("cancel"):
            raise TaskCancelledError("task was cancelled")

    async def _handoff(
        self, task: dict, queue: UploadQueue, stage: TaskStage, total: int | None
    ) -> None:
        await self._checkpoint(task)
        await self.stage(
            task, stage, total, waiting={"reason": f"queue:{queue}"}, step=queue.step
        )
        current = await self._store.get(task["id"])
        async with self._runtime.handoff_lock:
            await self._store.join_queue(task["id"], queue, current["priority_level"])
            await enqueue(
                queue,
                current["priority_level"],
                task_id=str(task["id"]),
                generation=current.get("generation") or 0,
            )

    async def _download(self, task: dict) -> None:
        params = task["params"]
        await self.stage(task, TaskStage.DOWNLOADING, None, step=TaskStep.DOWNLOADING)
        await self._disk_slot(task)
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
        await self._handoff(task, UploadQueue.PREPARE, TaskStage.SCANNING, None)

    async def _disk_slot(self, task: dict) -> None:
        if await self._store.take_disk_slot(task["id"]):
            return
        await self._store.update(task["id"], waiting={"reason": "disk_slots"})
        self.hook(task)
        while not await self._store.take_disk_slot(task["id"]):
            await self._checkpoint(task)
            await asyncio.sleep(DISK_SLOT_POLL)
        await self._store.update(task["id"], waiting=None)

    async def _prepare(self, task: dict) -> None:
        prepared = task.get("prepared") or {}
        encrypted = (
            prepared.get("encryption_header") and Path(prepared["send_path"]).exists()
        )
        if not encrypted:
            source = Path(task["params"]["file"])
            await self.stage(
                task,
                TaskStage.SCANNING,
                source.stat().st_size,
                step=TaskStep.ANALYZING,
            )
            prepared = await self._scan(task, source)
            if task["params"].get("encrypt"):
                prepared = await self._encrypt(task, prepared)
            await self._store.update(task["id"], prepared=prepared)
        await self._handoff(
            task,
            UploadQueue.TELEGRAM,
            TaskStage.UPLOADING_TG,
            Path(prepared["send_path"]).stat().st_size,
        )
        if task["params"].get("encrypt"):
            Path(task["params"]["file"]).unlink(missing_ok=True)

    async def _encrypt(self, task: dict, prepared: dict) -> dict:
        source = Path(prepared["send_path"])
        plain_size = source.stat().st_size
        target = temp_path("file.bin")
        prepared = {**prepared, "send_path": str(target), "plain_size": plain_size}
        await self._store.update(task["id"], prepared=prepared)
        await self.step(task, TaskStep.ENCRYPTING, plain_size)
        reservation = self._runtime.disk.reserve(cipher_size(plain_size))
        try:
            header = await asyncio.to_thread(
                encrypt_file,
                source,
                target,
                lambda done: self.progress(task["id"], done, plain_size),
            )
        finally:
            reservation.release()
        return {
            **prepared,
            "encryption_header": base64.b64encode(header).decode(),
            "thumbnail": await self._preview(task, prepared, source, header),
        }

    async def _preview(
        self, task: dict, prepared: dict, source: Path, header: bytes
    ) -> str | None:
        kind = prepared["media"]["kind"]
        if kind == MediaKind.VIDEO:
            source = prepared["thumb"] and Path(prepared["thumb"])
        if kind not in PREVIEW_KINDS or not source:
            return None
        async with self._runtime.session_factory() as session:
            if not (await root_settings(session)).uploads.previews:
                return None
        await self.step(task, TaskStep.PREVIEWING)
        try:
            data = await asyncio.to_thread(preview, source)
        except Exception as error:
            logger.warning("Task {}: no preview: {!r}", task["id"], error)
            return None
        return base64.b64encode(seal(header, data)).decode()

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
        deadline = time.monotonic() + seconds
        try:
            while (left := deadline - time.monotonic()) > 0:
                self._check_cancel(task["id"])
                await asyncio.sleep(min(left, CANCEL_POLL))
        finally:
            await self._store.update(task["id"], waiting=None)

    async def _scan(self, task: dict, path: Path) -> dict:
        _, _, name = split_file(task["path"])
        size = path.stat().st_size
        media = await asyncio.to_thread(probe, path, name)
        self._check_cancel(task["id"])
        sha256 = await asyncio.to_thread(
            sha256_of, path, lambda done: self.progress(task["id"], done, size)
        )
        await self.step(task, TaskStep.SCANNING, size)
        warnings: list[dict] = []
        try:
            await self._retry(
                task,
                lambda: self._runtime.scanner.scan(
                    path, lambda done: self.progress(task["id"], done, size)
                ),
                ScannerUnavailableError,
            )
        except UnscannableFileError as error:
            if not task["params"].get("allow_unscannable"):
                raise
            warnings.append(
                task_warning(
                    "unscannable_accepted",
                    f"{error.message}, accepted by allow_unscannable",
                    **error.extra,
                )
            )
        as_file = task["params"]["send_as"] == "file"
        if media.kind == MediaKind.DOCUMENT and not as_file:
            as_file = True
            warnings.append(
                task_warning(
                    "not_media_sent_as_file", "file is not media, sent as file"
                )
            )
        prepared = {"send_path": str(path), "thumb": None}
        if not as_file and media.kind == MediaKind.PHOTO:
            if not photo_fits(media, path.stat().st_size):
                as_file = True
                warnings.append(
                    task_warning(
                        "photo_too_big_sent_as_file",
                        "photo does not fit Telegram photo limits (10 MB, sides), sent as file",
                    )
                )
            elif media.mime not in TELEGRAM_PHOTO_SUFFIXES:
                await self.step(task, TaskStep.CONVERTING)
                send_path = temp_path("photo.jpg")
                prepared["send_path"] = str(send_path)
                await self._store.update(task["id"], prepared=prepared)
                await asyncio.to_thread(to_jpeg, path, send_path)
            elif path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                send_path = temp_path(f"photo{TELEGRAM_PHOTO_SUFFIXES[media.mime]}")
                prepared["send_path"] = str(send_path)
                await self._store.update(task["id"], prepared=prepared)
                await asyncio.to_thread(os.link, path, send_path)
        if not as_file and media.kind == MediaKind.VIDEO and media.mime != "video/mp4":
            warnings.append(
                task_warning(
                    "video_may_show_as_file",
                    f"{media.mime} may be shown as a file instead of a player in Telegram",
                    mime=media.mime,
                )
            )
        if media.kind == MediaKind.VIDEO:
            await self.step(task, TaskStep.CONVERTING)
            thumb = temp_path("thumb.jpg")
            prepared["thumb"] = str(thumb)
            await self._store.update(task["id"], prepared=prepared)
            if not await asyncio.to_thread(thumbnail, path, thumb, media.duration):
                prepared["thumb"] = None
        return {
            **prepared,
            "media": asdict(media),
            "sha256": sha256,
            "as_file": as_file,
            "warnings": warnings,
        }

    async def _location(
        self, storage_id: UUID, folder: str, name: str
    ) -> tuple[FileDB | None, int]:
        async with self._runtime.session_factory() as session:
            storage = await StorageRepository(session).get_by_id(storage_id)
            existing = await FileRepository(session).get_by_path(
                storage_id, folder, name
            )
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

    async def _send(self, task: dict) -> None:
        await self.step(task, TaskStep.SENDING, task.get("bytes_total"))
        prepared = task["prepared"]
        on_conflict = task["params"]["on_conflict"]
        storage_id, folder, name = split_file(task["path"])
        existing, chat_id = await self._location(storage_id, folder, name)
        telegram = await self._runtime.telegram()
        recorded = task.get("sent")
        planned = UUID(recorded["file_id"]) if recorded else None
        if recorded and existing and existing.id == planned:
            if existing.tg_message_id == recorded["message"]["message_id"]:
                await self.finish(task, planned, prepared["warnings"])
                return
        if existing and existing.id != planned:
            if on_conflict in ("error", "keep_both"):
                raise PathConflictError(f"{name} already exists")
            if on_conflict == "skip":
                if recorded:
                    await telegram.delete(chat_id, [recorded["message"]["message_id"]])
                await self.finish(task, existing.id, [SKIPPED])
                return
        file_id = planned or (existing.id if existing else uuid7())
        media = Media(**prepared["media"])
        as_file = prepared["as_file"]
        header = prepared.get("encryption_header")
        if recorded:
            sent = SentFile(**recorded["message"])
        else:
            await self._checkpoint(task)
            sent = await self._send_to_telegram(
                task,
                chat_id=chat_id,
                path=Path(prepared["send_path"]),
                name=f"{file_id}.bin" if header else name,
                caption=caption_token(file_id),
                media=media,
                as_file=as_file,
                thumb=(
                    Path(prepared["thumb"])
                    if prepared["thumb"] and not header
                    else None
                ),
                progress=lambda done, total: self.progress(task["id"], done, total),
            )
            await self._store.update(
                task["id"],
                sent={"file_id": file_id, "chat_id": chat_id, "message": asdict(sent)},
            )
            await self._checkpoint(task)
        await self.step(task, TaskStep.FINISHING)
        compressed = media.kind == MediaKind.PHOTO and not as_file
        values = dict(
            folder_path=folder,
            name=name,
            size=prepared["plain_size"] if header else sent.size,
            mime=(sent.mime or "image/jpeg") if compressed else media.mime,
            kind=media.kind,
            sent_as="file" if as_file else "media",
            width=sent.width or media.width,
            height=sent.height or media.height,
            duration=media.duration,
            sha256=prepared["sha256"],
            tg_message_id=sent.message_id,
            encryption_header=base64.b64decode(header) if header else None,
            thumbnail=(
                base64.b64decode(prepared["thumbnail"])
                if prepared.get("thumbnail")
                else None
            ),
        )
        async with self._runtime.session_factory() as session:
            await FolderRepository(session).ensure(storage_id, folder)
            if existing and existing.id == file_id:
                await FileRepository(session).update(file_id, values)
            else:
                await FileRepository(session).create(
                    FileDB(id=file_id, storage_id=storage_id, **values)
                )
            await session.commit()
        await self._store.update(task["id"], committed=True)
        if (
            existing
            and existing.id == file_id
            and existing.tg_message_id != sent.message_id
        ):
            await telegram.delete(chat_id, [existing.tg_message_id])
        await self.finish(task, file_id, prepared["warnings"])

    async def _archive(self, task: dict) -> None:
        params = task["params"]
        await self.stage(
            task, TaskStage.ARCHIVING, task["bytes_total"], step=TaskStep.ARCHIVING
        )
        async with self._runtime.session_factory() as session:
            found = await FileRepository(session).get_many_with_chat(
                [UUID(file_id) for file_id in params["file_ids"]]
            )
            storage = await StorageRepository(session).get_by_id(
                UUID(params["storage_id"])
            )
        files = sorted(
            (file for file, _ in found.values()),
            key=lambda file: (file.folder_path, file.name),
        )
        missing = len(params["file_ids"]) - len(files)
        sizes = [file.size for file in files]
        reservation = self._runtime.disk.reserve(sum(sizes) + max(sizes, default=0))
        try:
            pause = self._runtime.telegram_resume_at - time.monotonic()
            if pause > 0:
                await self._wait(task, "flood_wait", pause)
            warnings = await build_archive(
                await self._runtime.telegram(),
                storage.tg_chat_id,
                files,
                params["folder"],
                params["format"],
                Path(params["file"]),
                lambda done: self.progress(task["id"], done),
                lambda: self._checkpoint(task),
            )
        finally:
            reservation.release()
        if missing:
            warnings.append(
                task_warning(
                    "archive_files_deleted",
                    f"{missing} files were deleted before archiving",
                    count=missing,
                )
            )
        await self._checkpoint(task)
        archive = Path(params["file"])
        until = time.time() + settings.upload.ARCHIVE_TTL
        await self._store.keep_archive(str(archive), until)
        await self._store.update(
            task["id"],
            params={**params, "file": None},
            archive={
                **task["archive"],
                "path": str(archive),
                "size": archive.stat().st_size,
                "expires_at": datetime.fromtimestamp(until, UTC),
            },
        )
        await self._close(
            task,
            status=TaskStatus.DONE,
            stage=TaskStage.DONE,
            warnings=warnings,
            bytes_done=task["bytes_total"],
        )
        logger.info("Task {} archive ready: {}", task["id"], task["path"])

    async def stage(
        self,
        task: dict,
        stage: TaskStage,
        total: int | None,
        waiting: dict | None = None,
        step: TaskStep | None = None,
    ) -> None:
        await self._flush_progress(task["id"])
        await self._store.update(
            task["id"],
            stage=stage,
            step=step,
            bytes_done=0,
            bytes_total=total,
            waiting=waiting,
            stage_started=time.time(),
        )
        self.hook(task)

    async def step(self, task: dict, step: TaskStep, total: int | None = None) -> None:
        await self._flush_progress(task["id"])
        values = {"step": step}
        if step in TaskStep.measured():
            values.update(bytes_done=0, bytes_total=total, stage_started=time.time())
        await self._store.update(task["id"], **values)
        self.hook(task)

    def progress(self, task_id: str, done: int, total: int | None = None) -> None:
        self._check_cancel(task_id)
        now = time.monotonic()
        if now - self._progress_at.get(task_id, 0) < settings.upload.WEBHOOK_INTERVAL:
            return
        self._progress_at[task_id] = now
        values = {"bytes_done": done}
        if total:
            values["bytes_total"] = total
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._loop.call_soon_threadsafe(self._save_later, task_id, values)
        else:
            self._save_later(task_id, values)

    def _save_later(self, task_id: str, values: dict) -> None:
        self._progress_save[task_id] = self._runtime.spawn(
            self._save_progress(task_id, values)
        )

    async def _save_progress(self, task_id: str, values: dict) -> None:
        await self._store.update(task_id, **values)
        task = await self._store.get(task_id)
        if task and task.get("cancel"):
            self._cancelled.add(task_id)
        if task:
            self.hook(task)

    async def _flush_progress(self, task_id: str) -> None:
        self._progress_at.pop(task_id, None)
        pending = self._progress_save.pop(task_id, None)
        if pending:
            await asyncio.gather(pending, return_exceptions=True)

    async def _close(self, task: dict, **values) -> None:
        await self._flush_progress(task["id"])
        remove_task_files(await self._store.get(task["id"]) or task)
        await self._store.update(task["id"], waiting=None, **values)
        await self._store.release(task["id"], task["path"])
        self.hook(task)

    async def finish(self, task: dict, file_id: UUID, warnings: list[dict]) -> None:
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
        current = await self._store.get(task["id"]) or task
        try:
            recorded = current.get("sent")
            if recorded and not current.get("committed"):
                telegram = await self._runtime.telegram()
                await telegram.delete(
                    recorded["chat_id"], [recorded["message"]["message_id"]]
                )
                await self._store.update(task["id"], sent=None)
            if current.get("cancel") or isinstance(error, TaskCancelledError):
                logger.info("Task {} cancelled", task["id"])
                await asyncio.shield(
                    self._close(
                        task,
                        status=TaskStatus.CANCELLED,
                        error=TaskCancelledError("task was cancelled").to_dict(),
                    )
                )
                return
            details = describe(error)
            if details["code"] == "internal":
                logger.opt(exception=error).error("Task {} failed", task["id"])
            else:
                logger.warning("Task {} failed: {}", task["id"], details["message"])
            await asyncio.shield(
                self._close(task, status=TaskStatus.FAILED, error=details)
            )
        except Exception:
            logger.exception("Cannot save failed task {}", task["id"])

    def hook(self, task: dict) -> None:
        url = task["params"].get("callback_url")
        if url:
            self._runtime.spawn(
                send_hook(self._store, self._runtime.session_factory, task["id"], url)
            )
