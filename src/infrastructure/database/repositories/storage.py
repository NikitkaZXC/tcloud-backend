from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import FileDB, StorageDB


class StorageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, storage_id: UUID) -> StorageDB | None:
        return await self._session.get(StorageDB, storage_id, populate_existing=True)

    async def exists_chat(self, chat_id: int) -> bool:
        return bool(
            await self._session.scalar(
                select(StorageDB.id).where(StorageDB.tg_chat_id == chat_id)
            )
        )

    async def get_all(self) -> list[StorageDB]:
        result = await self._session.scalars(
            select(StorageDB).order_by(StorageDB.created_at, StorageDB.id)
        )
        return list(result.all())

    async def create(self, chat_id: int) -> StorageDB:
        storage = StorageDB(tg_chat_id=chat_id)
        self._session.add(storage)
        await self._session.flush()
        return storage

    async def touch(self, storage_id: UUID) -> None:
        await self._session.execute(
            update(StorageDB)
            .where(StorageDB.id == storage_id)
            .values(updated_at=func.now())
        )

    async def get_stats(self, storage_id: UUID) -> tuple[int, int]:
        statement = select(func.count(), func.coalesce(func.sum(FileDB.size), 0)).where(
            FileDB.storage_id == storage_id
        )
        count, total = (await self._session.execute(statement)).one()
        return count, total
