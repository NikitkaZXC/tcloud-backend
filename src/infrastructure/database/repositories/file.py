from datetime import date, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, case, delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import FileDB, StorageDB
from src.infrastructure.database.repositories.like import (
    escape_like,
    folder_and_below,
    readable,
)


def in_scope(
    scope: tuple[UUID, str] | None, scopes: list[tuple[UUID, str, bool]] | None
) -> list[ColumnElement[bool]]:
    conditions = [readable(FileDB.storage_id, FileDB.folder_path, scopes)]
    if scope:
        conditions.append(FileDB.storage_id == scope[0])
        if scope[1]:
            conditions.append(folder_and_below(FileDB.folder_path, scope[1]))
    return conditions


def contains(column: ColumnElement[str], query: str) -> ColumnElement[bool]:
    return func.lower(column).like(
        func.lower("%" + escape_like(query) + "%"), escape="\\"
    )


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

    async def get_missing_previews(
        self, kinds: list[str], after: tuple[str, UUID] | None, limit: int
    ) -> list[tuple[FileDB, int]]:
        statement = (
            select(FileDB, StorageDB.tg_chat_id)
            .join(StorageDB)
            .where(
                FileDB.encryption_header.is_not(None),
                FileDB.thumbnail.is_(None),
                FileDB.kind.in_(kinds),
            )
            .order_by(FileDB.kind, FileDB.id)
            .limit(limit)
        )
        if after:
            statement = statement.where(tuple_(FileDB.kind, FileDB.id) > after)
        return [
            (row[0], row[1]) for row in (await self._session.execute(statement)).all()
        ]

    async def set_thumbnail(self, file_id: UUID, data: bytes) -> None:
        await self._session.execute(
            update(FileDB)
            .where(FileDB.id == file_id, FileDB.thumbnail.is_(None))
            .values(thumbnail=data, updated_at=FileDB.updated_at)
        )
        await self._session.flush()

    async def clear_thumbnails(self, limit: int) -> tuple[int, int]:
        batch = (
            select(FileDB.id, func.octet_length(FileDB.thumbnail).label("size"))
            .where(FileDB.thumbnail.is_not(None))
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("batch")
        )
        result = await self._session.execute(
            update(FileDB)
            .where(FileDB.id == batch.c.id)
            .values(thumbnail=None, updated_at=FileDB.updated_at)
            .returning(batch.c.size)
        )
        sizes = list(result.scalars().all())
        await self._session.flush()
        return len(sizes), sum(sizes)

    async def get_thumbnail(self, file_id: UUID) -> bytes | None:
        statement = select(FileDB.thumbnail).where(FileDB.id == file_id)
        return await self._session.scalar(statement)

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

    async def search(
        self,
        *,
        scope: tuple[UUID, str] | None,
        scopes: list[tuple[UUID, str, bool]] | None,
        query: str | None,
        kinds: list[str],
        mimes: list[str],
        size_min: int | None,
        size_max: int | None,
        date_from: datetime | None,
        date_to: datetime | None,
        encrypted: bool | None,
        sort: str,
        lang: str,
        descending: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[FileDB, int]], int]:
        conditions = in_scope(scope, scopes)
        if query:
            conditions.append(contains(FileDB.name, query))
        if kinds:
            conditions.append(FileDB.kind.in_(kinds))
        if mimes:
            conditions.append(FileDB.mime.in_(mimes))
        if size_min is not None:
            conditions.append(FileDB.size >= size_min)
        if size_max is not None:
            conditions.append(FileDB.size <= size_max)
        if date_from is not None:
            conditions.append(FileDB.updated_at >= date_from)
        if date_to is not None:
            conditions.append(FileDB.updated_at <= date_to)
        if encrypted is not None:
            conditions.append(
                FileDB.encryption_header.is_not(None)
                if encrypted
                else FileDB.encryption_header.is_(None)
            )
        total = await self._session.scalar(
            select(func.count()).select_from(FileDB).where(*conditions)
        )
        column = {
            "size": FileDB.size,
            "date": FileDB.updated_at,
        }.get(sort, FileDB.name.collate(f"tcloud_{lang}"))
        order = [column.desc() if descending else column.asc()]
        if sort == "relevance" and query:
            lowered = func.lower(FileDB.name)
            order.insert(
                0,
                case(
                    (lowered == func.lower(query), 0),
                    (
                        lowered.like(func.lower(escape_like(query) + "%"), escape="\\"),
                        1,
                    ),
                    else_=2,
                ),
            )
        statement = (
            select(FileDB, StorageDB.tg_chat_id)
            .join(StorageDB)
            .where(*conditions)
            .order_by(*order, FileDB.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(statement)).all()
        return [(row[0], row[1]) for row in rows], total or 0

    async def activity(
        self,
        scope: tuple[UUID, str] | None,
        scopes: list[tuple[UUID, str, bool]] | None,
        since: datetime,
    ) -> list[tuple[date, int, int]]:
        day = func.date(func.timezone("UTC", FileDB.created_at)).label("day")
        statement = (
            select(day, func.count(), func.coalesce(func.sum(FileDB.size), 0))
            .where(*in_scope(scope, scopes), FileDB.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
        return [
            (row[0], row[1], row[2])
            for row in (await self._session.execute(statement)).all()
        ]

    async def mime_counts(
        self,
        scope: tuple[UUID, str] | None,
        scopes: list[tuple[UUID, str, bool]] | None,
        limit: int,
    ) -> list[tuple[str, int]]:
        count = func.count()
        statement = (
            select(FileDB.mime, count)
            .where(*in_scope(scope, scopes))
            .group_by(FileDB.mime)
            .order_by(count.desc(), FileDB.mime)
            .limit(limit)
        )
        return [
            (row[0], row[1]) for row in (await self._session.execute(statement)).all()
        ]

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
