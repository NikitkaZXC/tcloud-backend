from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import FolderGrantDB


class GrantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_user(self, user_id: UUID) -> list[FolderGrantDB]:
        result = await self._session.scalars(
            select(FolderGrantDB)
            .where(FolderGrantDB.user_id == user_id)
            .order_by(FolderGrantDB.storage_id, FolderGrantDB.path)
        )
        return list(result.all())

    async def get_by_id(self, grant_id: UUID) -> FolderGrantDB | None:
        return await self._session.get(FolderGrantDB, grant_id, populate_existing=True)

    async def get_by_path(
        self, user_id: UUID, storage_id: UUID, path: str
    ) -> FolderGrantDB | None:
        return await self._session.scalar(
            select(FolderGrantDB).where(
                FolderGrantDB.user_id == user_id,
                FolderGrantDB.storage_id == storage_id,
                FolderGrantDB.path == path,
            )
        )

    async def create(self, **values) -> FolderGrantDB:
        grant = FolderGrantDB(**values)
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def delete(self, grant: FolderGrantDB) -> None:
        await self._session.delete(grant)
        await self._session.flush()
