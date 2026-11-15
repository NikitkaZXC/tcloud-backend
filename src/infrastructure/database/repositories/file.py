from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import FileDB, StorageDB
from src.infrastructure.database.repositories.like import folder_and_below


class FileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_with_chat(self, file_id: UUID) -> tuple[FileDB, int] | None:
        statement = (
            select(FileDB, StorageDB.tg_chat_id)
            .join(StorageDB)
            .where(FileDB.id == file_id)
        )
        row = (await self._session.execute(statement)).first()
        return (row[0], row[1]) if row else None

    async def get_many_with_chat(
        self, file_ids: list[UUID]
    ) -> dict[UUID, tuple[FileDB, int]]:
        if not file_ids:
            return {}
        statement = (
            select(FileDB, StorageDB.tg_chat_id)
            .join(StorageDB)
            .where(FileDB.id.in_(file_ids))
        )
        return {
            row[0].id: (row[0], row[1])
            for row in (await self._session.execute(statement)).all()
        }

    async def get_by_path(
        self, storage_id: UUID, folder: str, name: str
    ) -> FileDB | None:
        statement = select(FileDB).where(
            FileDB.storage_id == storage_id,
            FileDB.folder_path == folder,
            FileDB.name == name,
        )
        return await self._session.scalar(statement)

    async def get_in_folder(self, storage_id: UUID, folder: str) -> list[FileDB]:
        statement = (
            select(FileDB)
            .where(FileDB.storage_id == storage_id, FileDB.folder_path == folder)
            .order_by(FileDB.name)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_messages_below(
        self, storage_id: UUID, folder: str
    ) -> list[tuple[UUID, int]]:
        statement = select(FileDB.id, FileDB.tg_message_id).where(
            FileDB.storage_id == storage_id,
            folder_and_below(FileDB.folder_path, folder),
        )
        return [
            (row[0], row[1]) for row in (await self._session.execute(statement)).all()
        ]

    async def create(self, file: FileDB) -> FileDB:
        self._session.add(file)
        await self._session.flush()
        return file

    async def update(self, file_id: UUID, values: dict) -> None:
        await self._session.execute(
            update(FileDB).where(FileDB.id == file_id).values(**values)
        )

    async def delete_by_ids(self, file_ids: list[UUID]) -> None:
        if file_ids:
            await self._session.execute(delete(FileDB).where(FileDB.id.in_(file_ids)))
