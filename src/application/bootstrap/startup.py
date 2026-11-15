from loguru import logger

from src.application.services import StorageService
from src.config import settings
from src.infrastructure.background import BackgroundRuntime
from src.infrastructure.database.repositories import TaskRepository
from src.infrastructure.database.session import session_factory

INTERRUPTED = {"code": "interrupted", "message": "server restarted"}


async def prepare(runtime: BackgroundRuntime) -> None:
    settings.upload.TMP_DIR.mkdir(parents=True, exist_ok=True)
    for item in settings.upload.TMP_DIR.iterdir():
        item.unlink(missing_ok=True)
    async with session_factory() as session:
        await TaskRepository(session).fail_active(INTERRUPTED)
        await session.commit()
        await StorageService(session, runtime).connect_chats(settings.telegram.CHAT_IDS)
    logger.info(
        "Startup done: temporary files cleaned, interrupted uploads marked failed"
    )
