import time

from loguru import logger

from src.application.services import StorageService
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.session import session_factory


async def prepare(runtime: AppRuntime) -> None:
    settings.upload.TMP_DIR.mkdir(parents=True, exist_ok=True)
    stale = time.time() - settings.upload.LOCK_TTL
    for item in settings.upload.TMP_DIR.iterdir():
        if item.stat().st_mtime < stale:
            item.unlink(missing_ok=True)
    async with session_factory() as session:
        await StorageService(session, runtime).connect_chats(settings.telegram.CHAT_IDS)
    logger.info("Startup done: stale temporary files cleaned")
