from collections.abc import Callable

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.schemas import Actor, SettingsServiceRead
from src.application.services.storage import StorageService
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.repositories import UserRepository
from src.infrastructure.enums import UserRole
from src.infrastructure.integrations.web import check_webhook


class SettingsService:
    def __init__(self, session: AsyncSession, runtime: AppRuntime) -> None:
        self._session = session
        self._runtime = runtime
        self._users = UserRepository(session)

    async def _save(
        self,
        actor: Actor,
        build: Callable[[SettingsServiceRead], SettingsServiceRead],
    ) -> SettingsServiceRead:
        user = await self._users.get_for_update(actor.user_id)
        current, _ = SettingsServiceRead.from_stored(user.settings)
        value = build(current)
        user.settings = value.model_dump(mode="json", exclude_unset=True)
        await self._users.save()
        await self._session.commit()
        if (
            actor.role == UserRole.ROOT
            and current.telegram.chat_folder != value.telegram.chat_folder
        ):
            await StorageService(self._session, self._runtime).sync_folder()
        return value

    @staticmethod
    def _merge(
        current: SettingsServiceRead, patch: SettingsServiceRead
    ) -> SettingsServiceRead:
        value = current.merged(patch)
        if value.uploads.webhook_url:
            check_webhook(value.uploads.webhook_url)
        return value

    async def get(self, actor: Actor) -> SettingsServiceRead:
        return actor.settings

    async def update(
        self, actor: Actor, patch: SettingsServiceRead
    ) -> SettingsServiceRead:
        value = await self._save(actor, lambda current: self._merge(current, patch))
        logger.info(
            "User {} changed settings: {}",
            actor.user_id,
            sorted(patch.model_dump(exclude_unset=True)),
        )
        return value

    async def reset(self, actor: Actor) -> SettingsServiceRead:
        value = await self._save(actor, lambda _: SettingsServiceRead())
        logger.info("User {} reset settings", actor.user_id)
        return value
