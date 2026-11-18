from uuid import UUID, uuid7

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import FolderDB
from src.infrastructure.database.repositories.like import (
    escape_like,
    folder_and_below,
    readable,
)


class FolderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_path(self, storage_id: UUID, path: str) -> FolderDB | None:
        statement = select(FolderDB).where(
            FolderDB.storage_id == storage_id, FolderDB.path == path
        )
        return await self._session.scalar(statement)

    async def exists(self, storage_id: UUID, path: str) -> bool:
        if not path:
            return True
        statement = select(FolderDB.id).where(
            FolderDB.storage_id == storage_id, FolderDB.path == path
        )
        return bool(await self._session.scalar(statement))

    async def ensure(self, storage_id: UUID, path: str) -> None:
        if not path:
            return
        segments = path.split("/")
        rows = [
            {
                "id": uuid7(),
                "storage_id": storage_id,
                "path": "/".join(segments[:depth]),
            }
            for depth in range(1, len(segments) + 1)
        ]
        await self._session.execute(
            insert(FolderDB).values(rows).on_conflict_do_nothing()
        )

    async def get_children(self, storage_id: UUID, path: str) -> list[FolderDB]:
        prefix = escape_like(path + "/" if path else "")
        statement = (
            select(FolderDB)
            .where(
                FolderDB.storage_id == storage_id,
                FolderDB.path.like(prefix + "%", escape="\\"),
                FolderDB.path.not_like(prefix + "%/%", escape="\\"),
            )
            .order_by(FolderDB.path)
        )
        return list((await self._session.scalars(statement)).all())

    async def search(
        self,
        scope: tuple[UUID, str] | None,
        scopes: list[tuple[UUID, str, bool]] | None,
        query: str,
        limit: int,
    ) -> list[FolderDB]:
        pattern = func.lower("%" + escape_like(query) + "%")
        name = func.regexp_replace(FolderDB.path, "^.*/", "")
        statement = select(FolderDB).where(
            readable(FolderDB.storage_id, FolderDB.path, scopes),
            func.lower(FolderDB.path).like(pattern, escape="\\"),
            func.lower(name).like(pattern, escape="\\"),
        )
        if scope:
            statement = statement.where(FolderDB.storage_id == scope[0])
            if scope[1]:
                statement = statement.where(
                    FolderDB.path.like(escape_like(scope[1] + "/") + "%", escape="\\")
                )
        lowered = func.lower(name)
        statement = statement.order_by(
            (lowered == func.lower(query)).desc(),
            lowered.like(func.lower(escape_like(query) + "%"), escape="\\").desc(),
            name,
            FolderDB.path,
        ).limit(limit)
        return list((await self._session.scalars(statement)).all())

    async def delete_with_children(self, storage_id: UUID, path: str) -> None:
        statement = delete(FolderDB).where(
            FolderDB.storage_id == storage_id, folder_and_below(FolderDB.path, path)
        )
        await self._session.execute(statement)
