from datetime import datetime, timedelta
from uuid import uuid7

import pytest

from src.application.services import TaskSweeper
from src.application.services import sweeper as sweeper_module
from src.infrastructure.cache import TaskStore, encode

EMPTY = {"uploads.download": 0, "uploads.prepare": 0, "uploads.telegram": 0}


@pytest.fixture
def store(redis) -> TaskStore:
    return TaskStore(redis)


@pytest.fixture
def sizes(monkeypatch) -> dict:
    current = dict(EMPTY)

    async def queue_sizes():
        return current

    monkeypatch.setattr(sweeper_module, "queue_sizes", queue_sizes)
    return current


async def active(store, redis, tmp_path, *, age: float, queue="uploads.prepare"):
    task_id = uuid7()
    path = f"tcloud/x/{task_id}.jpg"
    file = tmp_path / f"{task_id}.jpg"
    file.write_bytes(b"x")
    await store.create(
        {
            "id": task_id,
            "status": "queued",
            "path": path,
            "waiting": {"reason": f"queue:{queue}"},
            "params": {"file": str(file)},
        }
    )
    await store.activate(task_id)
    await store.lock_path(path, task_id)
    if age:
        old = (await store.get(task_id))["updated_at"]
        moved = datetime.fromisoformat(old) - timedelta(seconds=age)
        await redis.hset(f"tcloud:task:{task_id}", "updated_at", encode(moved))
    return task_id, path, file


async def test_lost_task_is_failed_on_second_sweep(store, redis, sizes, tmp_path):
    task_id, path, file = await active(store, redis, tmp_path, age=600)
    sweeper = TaskSweeper(store)
    assert await sweeper.sweep() == 0
    assert (await store.get(task_id))["status"] == "queued"
    assert await sweeper.sweep() == 1
    task = await store.get(task_id)
    assert task["status"] == "failed" and task["error"]["code"] == "lost"
    assert not await store.path_locked(path) and await store.active_ids() == []
    assert not file.exists()


async def test_fresh_busy_and_beating_tasks_are_kept(store, redis, sizes, tmp_path):
    fresh, _, _ = await active(store, redis, tmp_path, age=0)
    busy, _, _ = await active(store, redis, tmp_path, age=600, queue="uploads.telegram")
    beating, _, _ = await active(store, redis, tmp_path, age=600)
    sizes["uploads.telegram"] = 3
    sweeper = TaskSweeper(store)
    for _ in range(3):
        await store.touch(beating)
        assert await sweeper.sweep() == 0
    for task_id in (fresh, busy, beating):
        assert (await store.get(task_id))["status"] == "queued"


async def test_suspect_that_recovers_is_not_failed(store, redis, sizes, tmp_path):
    task_id, _, _ = await active(store, redis, tmp_path, age=600)
    sweeper = TaskSweeper(store)
    await sweeper.sweep()
    await store.touch(task_id)
    await sweeper.sweep()
    await sweeper.sweep()
    assert (await store.get(task_id))["status"] == "queued"


async def test_finished_tasks_leave_active_set(store, redis, sizes, tmp_path):
    task_id, path, _ = await active(store, redis, tmp_path, age=0)
    await store.update(task_id, status="done")
    await TaskSweeper(store).sweep()
    assert await store.active_ids() == [] and not await store.path_locked(path)


async def test_broker_down_skips_sweep(store, redis, tmp_path, monkeypatch):
    async def down():
        return None

    monkeypatch.setattr(sweeper_module, "queue_sizes", down)
    task_id, _, _ = await active(store, redis, tmp_path, age=600)
    sweeper = TaskSweeper(store)
    await sweeper.sweep()
    await sweeper.sweep()
    assert (await store.get(task_id))["status"] == "queued"
