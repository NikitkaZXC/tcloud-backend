from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.schemas import HealthServiceRead
from src.infrastructure.background import AppRuntime, broker_alive
from src.infrastructure.enums import TelegramSession, UploadQueue
from src.infrastructure.integrations import session_exists


class HealthService:
    def __init__(self, session: AsyncSession, runtime: AppRuntime) -> None:
        self._session = session
        self._runtime = runtime

    async def check(self) -> HealthServiceRead:
        try:
            await self._session.execute(text("SELECT 1"))
            db = True
        except Exception:
            db = False
        telegram = (
            self._runtime.telegram.connected() and self._runtime.telegram.authorized
        )
        telegram_upload = session_exists(TelegramSession.UPLOAD)
        telegram_download = (
            self._runtime.download.connected() and self._runtime.download.authorized
        )
        antivirus = await self._runtime.scanner.ping()
        redis = await self._runtime.store.ping()
        queue = await broker_alive()
        alive = await self._runtime.store.workers() if redis else set()
        workers = {item.value: item.value in alive for item in UploadQueue}
        return HealthServiceRead(
            ok=db
            and telegram
            and telegram_upload
            and telegram_download
            and antivirus
            and redis
            and queue
            and all(workers.values()),
            db=db,
            telegram=telegram,
            telegram_upload=telegram_upload,
            telegram_download=telegram_download,
            antivirus=antivirus,
            redis=redis,
            queue=queue,
            workers=workers,
        )
