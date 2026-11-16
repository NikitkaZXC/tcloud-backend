from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import ApiKeyDB


class ApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: UUID, name: str, key_hash: str) -> ApiKeyDB:
        key = ApiKeyDB(user_id=user_id, name=name, key_hash=key_hash)
        self._session.add(key)
        await self._session.flush()
        return key

    async def get_by_hash(self, key_hash: str) -> ApiKeyDB | None:
        return await self._session.scalar(
            select(ApiKeyDB).where(ApiKeyDB.key_hash == key_hash)
        )

    async def get_by_id(self, key_id: UUID) -> ApiKeyDB | None:
        return await self._session.get(ApiKeyDB, key_id)

    async def get_by_user(self, user_id: UUID) -> list[ApiKeyDB]:
        result = await self._session.scalars(
            select(ApiKeyDB)
            .where(ApiKeyDB.user_id == user_id)
            .order_by(ApiKeyDB.created_at, ApiKeyDB.id)
        )
        return list(result.all())

    async def touch(self, key_id: UUID) -> None:
        await self._session.execute(
            update(ApiKeyDB)
            .where(ApiKeyDB.id == key_id)
            .values(last_used_at=datetime.now(UTC))
        )

    async def delete(self, key: ApiKeyDB) -> None:
        await self._session.delete(key)
        await self._session.flush()
