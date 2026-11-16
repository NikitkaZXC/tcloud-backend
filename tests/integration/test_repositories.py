import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.infrastructure.database.repositories import FolderRepository, StorageRepository


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def test_folders_ensure_is_idempotent_in_parallel(sessions):
    async with sessions() as session:
        storage = await StorageRepository(session).create(-1001)
        await session.commit()

    async def ensure() -> None:
        async with sessions() as session:
            await FolderRepository(session).ensure(storage.id, "a/b/c")
            await session.commit()

    await asyncio.gather(*(ensure() for _ in range(10)))
    async with sessions() as session:
        repo = FolderRepository(session)
        assert [folder.path for folder in await repo.get_children(storage.id, "")] == [
            "a"
        ]
        assert [
            folder.path for folder in await repo.get_children(storage.id, "a/b")
        ] == ["a/b/c"]
