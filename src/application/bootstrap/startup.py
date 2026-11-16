import time
from functools import partial

from loguru import logger

from src.application.services import FileService, StorageService
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.session import session_factory


async def forget_deleted(
    runtime: AppRuntime, chat_id: int, message_ids: list[int]
) -> None:
    try:
        async with session_factory() as session:
            files = FileService(session, runtime, StorageService(session, runtime))
            await files.forget_messages(chat_id, message_ids)
    except Exception:
        logger.exception("Could not forget messages deleted in {}", chat_id)


async def prepare(runtime: AppRuntime) -> None:
    settings.upload.TMP_DIR.mkdir(parents=True, exist_ok=True)
    stale = time.time() - settings.upload.LOCK_TTL
    for item in settings.upload.TMP_DIR.iterdir():
        if item.stat().st_mtime < stale:
            item.unlink(missing_ok=True)
    async with session_factory() as session:
        await StorageService(session, runtime).connect_chats(settings.telegram.CHAT_IDS)
    await runtime.telegram.watch_deletions(partial(forget_deleted, runtime))
    logger.info("Startup done: stale temporary files cleaned")
