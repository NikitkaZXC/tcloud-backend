from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import RefreshTokenDB


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **values) -> RefreshTokenDB:
        token = RefreshTokenDB(**values)
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_by_hash(self, token_hash: str) -> RefreshTokenDB | None:
        return await self._session.scalar(
            select(RefreshTokenDB)
            .where(RefreshTokenDB.token_hash == token_hash)
            .with_for_update()
        )

    async def get_by_id(self, token_id: UUID) -> RefreshTokenDB | None:
        return await self._session.get(RefreshTokenDB, token_id)

    async def get_active(self, user_id: UUID) -> list[RefreshTokenDB]:
        result = await self._session.scalars(
            select(RefreshTokenDB)
            .where(
                RefreshTokenDB.user_id == user_id,
                RefreshTokenDB.revoked.is_(False),
                RefreshTokenDB.expires_at > datetime.now(UTC),
            )
            .order_by(RefreshTokenDB.created_at.desc())
        )
        return list(result.all())

    async def delete_expired(self, user_id: UUID) -> None:
        await self._session.execute(
            delete(RefreshTokenDB).where(
                RefreshTokenDB.user_id == user_id,
                RefreshTokenDB.expires_at <= datetime.now(UTC),
            )
        )
