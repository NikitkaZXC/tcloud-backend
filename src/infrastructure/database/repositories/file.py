from uuid import UUID

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import FileDB, StorageDB
from src.infrastructure.database.repositories.like import folder_and_below, readable


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

    async def get_below(self, storage_id: UUID, folder: str) -> list[FileDB]:
        statement = (
            select(FileDB)
            .where(FileDB.storage_id == storage_id)
            .order_by(FileDB.folder_path, FileDB.name)
        )
        if folder:
            statement = statement.where(folder_and_below(FileDB.folder_path, folder))
        return list((await self._session.scalars(statement)).all())

    async def sizes_below(
        self,
        storage_id: UUID,
        folder: str,
        scopes: list[tuple[UUID, str, bool]] | None,
    ) -> dict[str | None, tuple[int, int]]:
        rest = func.substr(FileDB.folder_path, len(folder) + 2 if folder else 1)
        child = case(
            (FileDB.folder_path == folder, None),
            else_=func.split_part(rest, "/", 1),
        ).label("child")
        statement = (
            select(child, func.count(), func.coalesce(func.sum(FileDB.size), 0))
            .where(
                FileDB.storage_id == storage_id,
                readable(FileDB.storage_id, FileDB.folder_path, scopes),
            )
            .group_by(child)
        )
        if folder:
            statement = statement.where(folder_and_below(FileDB.folder_path, folder))
        return {
            row[0]: (row[1], row[2])
            for row in (await self._session.execute(statement)).all()
        }

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

    async def delete_by_messages(
        self, storage_id: UUID, message_ids: list[int]
    ) -> list[UUID]:
        if not message_ids:
            return []
        result = await self._session.execute(
            delete(FileDB)
            .where(
                FileDB.storage_id == storage_id,
                FileDB.tg_message_id.in_(message_ids),
            )
            .returning(FileDB.id)
        )
        return list(result.scalars().all())

    async def delete_by_ids(self, file_ids: list[UUID]) -> None:
        if file_ids:
            await self._session.execute(delete(FileDB).where(FileDB.id.in_(file_ids)))
