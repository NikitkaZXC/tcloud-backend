from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.schemas import HealthServiceRead
from src.infrastructure.background import AppRuntime


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
        telegram = self._runtime.telegram.connected()
        antivirus = await self._runtime.scanner.ping()
        redis = await self._runtime.store.ping()
        return HealthServiceRead(
            ok=db and telegram and antivirus and redis,
            db=db,
            telegram=telegram,
            antivirus=antivirus,
            redis=redis,
        )
