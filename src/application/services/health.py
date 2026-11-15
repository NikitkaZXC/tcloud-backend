from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.schemas import HealthServiceRead
from src.infrastructure.background import BackgroundRuntime


class HealthService:
    def __init__(self, session: AsyncSession, runtime: BackgroundRuntime) -> None:
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
        return HealthServiceRead(
            ok=db and telegram and antivirus,
            db=db,
            telegram=telegram,
            antivirus=antivirus,
        )
