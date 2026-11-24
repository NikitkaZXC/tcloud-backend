import asyncio
import time
from pathlib import Path

from loguru import logger

from src.application.services.file import (
    file_body,
    open_pieces,
    telegram_reader,
    tracked,
)
from src.application.services.pipeline import PREVIEW_KINDS
from src.application.services.storage import root_settings
from src.application.services.temp import temp_path, write_body
from src.infrastructure.background import WorkerRuntime
from src.infrastructure.database.models import FileDB
from src.infrastructure.database.repositories import FileRepository
from src.infrastructure.enums import MediaKind
from src.infrastructure.integrations import (
    TelegramFloodWaitError,
    TelegramUnauthorizedError,
)
from src.infrastructure.media import preview, thumbnail
from src.infrastructure.security import seal

BACKFILL_JOB = "previews"
BATCH = 100
RENEW_INTERVAL = 30


class PreviewBackfill:
    def __init__(self, runtime: WorkerRuntime) -> None:
        self._runtime = runtime
        self._store = runtime.store

    async def run(self, owner: str) -> None:
        if not await self._store.hold_job(BACKFILL_JOB, owner):
            logger.info("Preview backfill already running, {} skipped", owner)
            return
        renew = self._runtime.spawn(self._renew(owner))
        done = skipped = 0
        after = None
        try:
            while True:
                async with self._runtime.session_factory() as session:
                    if not (await root_settings(session)).uploads.previews:
                        logger.info("Preview backfill stopped: previews are off")
                        break
                    batch = await FileRepository(session).get_missing_previews(
                        [kind.value for kind in PREVIEW_KINDS], after, BATCH
                    )
                if not batch:
                    break
                for file, chat_id in batch:
                    after = (file.kind, file.id)
                    if await self._fill(file, chat_id):
                        done += 1
                    else:
                        skipped += 1
                    logger.info("Preview backfill: {} done, {} skipped", done, skipped)
        finally:
            renew.cancel()
            await self._store.release_job(BACKFILL_JOB, owner)
        logger.info("Preview backfill finished: {} done, {} skipped", done, skipped)

    async def _renew(self, owner: str) -> None:
        while True:
            await asyncio.sleep(RENEW_INTERVAL)
            await self._store.hold_job(BACKFILL_JOB, owner)

    async def _fill(self, file: FileDB, chat_id: int) -> bool:
        while True:
            pause = self._runtime.telegram_resume_at - time.monotonic()
            if pause > 0:
                await asyncio.sleep(pause)
                continue
            try:
                data = await self._make(file, chat_id)
            except TelegramFloodWaitError as error:
                self._runtime.telegram_resume_at = max(
                    self._runtime.telegram_resume_at, time.monotonic() + error.seconds
                )
                logger.warning(
                    "Preview backfill: Telegram flood wait {} s", error.seconds
                )
                continue
            except TelegramUnauthorizedError:
                await self._runtime.forget_telegram()
                raise
            except Exception as error:
                logger.warning("File {}: no preview: {!r}", file.id, error)
                return False
            async with self._runtime.session_factory() as session:
                await FileRepository(session).set_thumbnail(file.id, data)
                await session.commit()
            return True

    async def _make(self, file: FileDB, chat_id: int) -> bytes:
        telegram = await self._runtime.telegram()
        async with self._runtime.session_factory() as session:
            parts = await FileRepository(session).get_parts([file.id])
        pieces = await open_pieces(
            lambda message_id: telegram.message_media(chat_id, message_id),
            file,
            parts.get(file.id, []),
        )
        plain = temp_path(file.name)
        frame = temp_path("thumb.jpg")
        reservation = await self._runtime.disk.reserve(file.id, file.size)
        try:
            await write_body(
                tracked(
                    file_body(
                        telegram_reader(telegram), pieces, file, 0, file.size - 1
                    ),
                    self._store,
                    file.storage_id,
                    "read",
                ),
                plain,
                file.size,
                reservation.update,
            )
            source: Path = plain
            if file.kind == MediaKind.VIDEO:
                if not await asyncio.to_thread(thumbnail, plain, frame, file.duration):
                    raise ValueError("ffmpeg made no frame")
                source = frame
            data = await asyncio.to_thread(preview, source)
        finally:
            await reservation.release()
            plain.unlink(missing_ok=True)
            frame.unlink(missing_ok=True)
        return seal(file.encryption_header, data)
