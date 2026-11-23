import time

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.schemas import AccountServiceRead
from src.application.services.storage import root_settings
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.limits import CHANNELS_MAX, CHANNELS_MAX_PREMIUM, file_limit
from src.utilities.errors import TCloudError


class AccountService:
    def __init__(self, session: AsyncSession, runtime: AppRuntime) -> None:
        self._session = session
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

    async def channels_used(self) -> int | None:
        cached = self._runtime.channels
        if cached and time.monotonic() - cached[0] <= settings.upload.CHANNELS_TTL:
            return cached[1]
        try:
            used = await self._runtime.telegram.channels_used()
        except TCloudError as error:
            logger.warning("Telegram channels count is unavailable: {}", error.message)
            return None
        self._runtime.channels = (time.monotonic(), used)
        return used

    async def get_account(self) -> AccountServiceRead:
        previews = (await root_settings(self._session)).uploads.previews
        if not self._runtime.telegram.authorized:
            return AccountServiceRead(
                telegram_connected=False,
                premium=None,
                file_limit=None,
                channels_used=None,
                channels_limit=None,
                previews_enabled=previews,
            )
        premium = await self.is_premium(fresh=True)
        return AccountServiceRead(
            telegram_connected=True,
            premium=premium,
            file_limit=file_limit(premium),
            channels_used=await self.channels_used(),
            channels_limit=CHANNELS_MAX_PREMIUM if premium else CHANNELS_MAX,
            previews_enabled=previews,
        )
