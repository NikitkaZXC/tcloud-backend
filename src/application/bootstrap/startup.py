import time

from loguru import logger

from src.application.services import AuthService, UserAuthService, UserService
from src.application.services.temp import used_files
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.session import session_factory
from src.infrastructure.security import check_keys


async def prepare(runtime: AppRuntime) -> None:
    settings.upload.TMP_DIR.mkdir(parents=True, exist_ok=True)
    stale = time.time() - settings.upload.LOCK_TTL
    used = await used_files(runtime.store)
    for item in settings.upload.TMP_DIR.iterdir():
        if item not in used and item.stat().st_mtime < stale:
            item.unlink(missing_ok=True)
    check_keys()
    async with session_factory() as session:
        await UserService(
            session, UserAuthService(session, runtime), runtime
        ).ensure_root()
    if runtime.telegram.authorized:
        await AuthService(runtime).connect()
    else:
        logger.warning(
            "Telegram is not logged in: open /docs and log in with /api/telegram"
        )
    logger.info("Startup done: stale temporary files cleaned")
