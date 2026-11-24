from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.schemas import AccountServiceRead, Actor, WebhookSecretServiceRead
from src.application.services.storage import root_settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.limits import CHANNELS_MAX, CHANNELS_MAX_PREMIUM, file_limit
from src.infrastructure.security import webhook_key
from src.utilities.errors import TCloudError


class AccountService:
    def __init__(self, session: AsyncSession, runtime: AppRuntime) -> None:
        self._session = session
        self._runtime = runtime

    async def is_premium(self) -> bool:
        premium = await self._runtime.store.cached("account", "premium")
        if premium is None:
            premium = await self._runtime.telegram.is_premium()
            await self._runtime.store.keep(premium, "account", "premium")
        return premium

    async def channels_used(self) -> int | None:
        used = await self._runtime.store.cached("account", "channels")
        if used is not None:
            return used
        try:
            used = await self._runtime.telegram.channels_used()
        except TCloudError as error:
            logger.warning("Telegram channels count is unavailable: {}", error.message)
            return None
        await self._runtime.store.keep(used, "account", "channels")
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
        premium = await self.is_premium()
        return AccountServiceRead(
            telegram_connected=True,
            premium=premium,
            file_limit=file_limit(premium),
            channels_used=await self.channels_used(),
            channels_limit=CHANNELS_MAX_PREMIUM if premium else CHANNELS_MAX,
            previews_enabled=previews,
        )

    @staticmethod
    def webhook_secret(actor: Actor) -> WebhookSecretServiceRead:
        return WebhookSecretServiceRead(
            user_id=actor.user_id, secret=webhook_key(actor.user_id).hex()
        )
