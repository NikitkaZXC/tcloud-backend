import secrets
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import ApiKeyNotFoundError
from src.application.schemas import Actor, ApiKeyServiceRead
from src.application.services.user import UserService
from src.infrastructure.database.repositories import ApiKeyRepository
from src.infrastructure.security import hash_secret

KEY_PREFIX = "tc_"


class ApiKeyService:
    def __init__(self, session: AsyncSession, user_service: UserService) -> None:
        self._session = session
        self._users = user_service
        self._repo = ApiKeyRepository(session)

    async def create(self, actor: Actor, user_id: UUID, name: str) -> ApiKeyServiceRead:
        await self._users.get_manageable(actor, user_id, allow_self=True)
        raw = KEY_PREFIX + secrets.token_urlsafe(32)
        key = await self._repo.create(user_id, name, hash_secret(raw))
        await self._session.commit()
        logger.info(
            "API key {} issued to user {} by {}", key.id, user_id, actor.user_id
        )
        return ApiKeyServiceRead.from_db(key, raw)

    async def get_all(self, actor: Actor, user_id: UUID) -> list[ApiKeyServiceRead]:
        await self._users.get_manageable(actor, user_id, allow_self=True)
        return [
            ApiKeyServiceRead.from_db(key)
            for key in await self._repo.get_by_user(user_id)
        ]

    async def delete(self, actor: Actor, user_id: UUID, key_id: UUID) -> None:
        await self._users.get_manageable(actor, user_id, allow_self=True)
        key = await self._repo.get_by_id(key_id)
        if not key or key.user_id != user_id:
            raise ApiKeyNotFoundError("API key not found")
        await self._repo.delete(key)
        await self._session.commit()
        logger.info("API key {} revoked by {}", key_id, actor.user_id)
