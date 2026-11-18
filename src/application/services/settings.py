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

    async def _save(self, actor: Actor, value: SettingsServiceRead) -> None:
        user = await self._users.get_by_id(actor.user_id)
        user.settings = value.model_dump(mode="json", exclude_unset=True)
        await self._users.save()
        await self._session.commit()
        if (
            actor.role == UserRole.ROOT
            and actor.settings.telegram.chat_folder != value.telegram.chat_folder
        ):
            await StorageService(self._session, self._runtime).sync_folder()

    async def get(self, actor: Actor) -> SettingsServiceRead:
        return actor.settings

    async def update(
        self, actor: Actor, patch: SettingsServiceRead
    ) -> SettingsServiceRead:
        value = actor.settings.merged(patch)
        if value.uploads.webhook_url:
            check_webhook(value.uploads.webhook_url)
        await self._save(actor, value)
        logger.info(
            "User {} changed settings: {}",
            actor.user_id,
            sorted(patch.model_dump(exclude_unset=True)),
        )
        return value

    async def reset(self, actor: Actor) -> SettingsServiceRead:
        value = SettingsServiceRead()
        await self._save(actor, value)
        logger.info("User {} reset settings", actor.user_id)
        return value
