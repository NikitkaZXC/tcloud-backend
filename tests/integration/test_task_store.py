import asyncio
from uuid import uuid7

import pytest

from src.config import settings
from src.infrastructure.cache import TaskStore


@pytest.fixture
def store(redis) -> TaskStore:
    return TaskStore(redis)


async def test_create_update_get(store):
    task_id, batch_id = uuid7(), uuid7()
    await store.create(
        {"id": task_id, "batch_id": batch_id, "status": "queued", "params": {"a": 1}}
    )
    await store.update(task_id, status="running", bytes_done=5)
    task = await store.get(task_id)
    assert (task["id"], task["status"], task["bytes_done"]) == (
        str(task_id),
        "running",
        5,
    )
    assert task["params"] == {"a": 1} and task["updated_at"] >= task["created_at"]
    assert await store.get(uuid7()) is None


async def test_partial_updates_do_not_overwrite_each_other(store):
    task_id = uuid7()
    await store.create({"id": task_id, "status": "running", "bytes_done": 0})
    await asyncio.gather(
        store.update(task_id, status="done"), store.update(task_id, bytes_done=10)
    )
    task = await store.get(task_id)
    assert (task["status"], task["bytes_done"]) == ("done", 10)


async def test_batch_keeps_order(store):
    batch_id = uuid7()
    ids = [uuid7() for _ in range(3)]
    for task_id in ids:
        await store.create({"id": task_id, "batch_id": batch_id})
    assert await store.batch(batch_id) == [str(task_id) for task_id in ids]
    tasks = await store.get_many(await store.batch(batch_id) + [str(uuid7())])
    assert [task["id"] for task in tasks] == [str(task_id) for task_id in ids]


async def test_path_lock_is_exclusive_and_owned(store):
    first, second = uuid7(), uuid7()
    results = await asyncio.gather(
        store.lock_path("tcloud/x/a.jpg", first),
        store.lock_path("tcloud/x/a.jpg", second),
    )
    assert sorted(results) == [False, True]
    owner = first if results[0] else second
    other = second if owner == first else first
    await store.unlock_path("tcloud/x/a.jpg", other)
    assert await store.path_locked("tcloud/x/a.jpg")
    await store.unlock_path("tcloud/x/a.jpg", owner)
    assert not await store.path_locked("tcloud/x/a.jpg")


async def test_active_count_drops_stale_entries(store, monkeypatch):
    first, second = uuid7(), uuid7()
    await store.activate(first)
    await store.activate(second)
    assert await store.count_active() == 2
    await store.deactivate(first)
    assert await store.count_active() == 1
    monkeypatch.setattr(settings.upload, "LOCK_TTL", -1)
    assert await store.count_active() == 0


async def test_ping(store):
    assert await store.ping()
