import asyncio

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.infrastructure.database.models import TaskDB
from src.infrastructure.database.repositories import (
    FolderRepository,
    StorageRepository,
    TaskRepository,
)
from src.infrastructure.enums import TaskStage, TaskStatus


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def _task(target: str, status: TaskStatus = TaskStatus.PENDING) -> TaskDB:
    return TaskDB(path=target, status=status, stage=TaskStage.RECEIVING, params={})


async def test_one_active_upload_per_path_under_race(sessions):
    async def insert() -> object:
        async with sessions() as session:
            try:
                await TaskRepository(session).create(_task("tcloud/x/a.jpg"))
                await asyncio.sleep(0.05)
                await session.commit()
                return "ok"
            except IntegrityError as error:
                return error

    results = await asyncio.gather(insert(), insert())
    assert (
        results.count("ok") == 1
        and sum(isinstance(item, IntegrityError) for item in results) == 1
    )


async def test_finished_tasks_do_not_block_path(sessions):
    async with sessions() as session:
        repo = TaskRepository(session)
        await repo.create(_task("tcloud/x/a.jpg", TaskStatus.DONE))
        await repo.create(_task("tcloud/x/a.jpg", TaskStatus.FAILED))
        await repo.create(_task("tcloud/x/a.jpg"))
        await session.commit()


async def test_claim_is_atomic(sessions):
    async with sessions() as session:
        task = await TaskRepository(session).create(_task("tcloud/x/b.jpg"))
        await session.commit()

    async def claim() -> bool:
        async with sessions() as session:
            claimed = await TaskRepository(session).claim(task.id)
            await session.commit()
            return claimed

    assert sorted(await asyncio.gather(*(claim() for _ in range(5)))) == [False] * 4 + [
        True
    ]


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
