import time

from src.application.schemas import AccountServiceRead
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.limits import file_limit


class AccountService:
    def __init__(self, runtime: AppRuntime) -> None:
        self._runtime = runtime

    async def is_premium(self, fresh: bool = False) -> bool:
        cached = self._runtime.premium
        if (
            fresh
            or not cached
            or time.monotonic() - cached[0] > settings.upload.PREMIUM_TTL
        ):
            self._runtime.premium = (
                time.monotonic(),
                await self._runtime.telegram.is_premium(),
            )
        return self._runtime.premium[1]

    async def file_limit(self) -> int:
        return file_limit(await self.is_premium())

    async def get_account(self) -> AccountServiceRead:
        premium = await self.is_premium(fresh=True)
        return AccountServiceRead(premium=premium, file_limit=file_limit(premium))
