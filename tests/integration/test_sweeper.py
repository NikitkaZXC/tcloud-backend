import asyncio
import os
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


async def test_orphaned_temp_files_are_removed(store, redis, tmp_path, monkeypatch):
    monkeypatch.setattr(sweeper_module.settings.upload, "TMP_DIR", tmp_path)
    monkeypatch.setattr(sweeper_module.settings.upload, "ORPHAN_AGE", 3600)
    _, _, owned = await active(store, redis, tmp_path, age=0)
    orphan, fresh = tmp_path / "orphan.jpg", tmp_path / "fresh.jpg"
    orphan.write_bytes(b"x")
    fresh.write_bytes(b"x")
    hour_ago = datetime.now().timestamp() - 7200
    for item in (owned, orphan):
        os.utime(item, (hour_ago, hour_ago))
    assert await TaskSweeper(store).clean_tmp() == 1
    assert owned.exists() and fresh.exists() and not orphan.exists()


async def test_missing_task_leaves_active_set(store, sizes):
    await store.activate(uuid7())
    await TaskSweeper(store).sweep()
    assert await store.active_ids() == []


async def test_claimed_task_is_judged_by_its_claimed_queue(
    store, redis, sizes, tmp_path
):
    task_id, path, _ = await active(store, redis, tmp_path, age=600)
    await redis.hset(f"tcloud:task:{task_id}", "waiting", "null")
    await redis.hset(f"tcloud:task:{task_id}", "claimed", encode("uploads.telegram"))
    sizes["uploads.telegram"] = 1
    sweeper = TaskSweeper(store)
    await sweeper.sweep()
    await sweeper.sweep()
    assert (await store.get(task_id))["status"] == "queued"
    sizes["uploads.telegram"] = 0
    await sweeper.sweep()
    await sweeper.sweep()
    assert (await store.get(task_id))["status"] == "failed"


async def session(store, tmp_path, until: float) -> tuple[str, object]:
    task_id = uuid7()
    file = tmp_path / f"{task_id}.part"
    file.write_bytes(b"x")
    task = {
        "id": str(task_id),
        "path": f"tcloud/x/{task_id}",
        "user_id": str(uuid7()),
        "bytes_total": 10,
        "params": {"file": str(file), "encrypt": False},
    }
    await store.save_session(
        {"task": task, "offset": 1, "started_at": until, "expires_at": until}
    )
    return str(task_id), file


async def test_expired_sessions_busy_missing_and_renewed(
    store, redis, tmp_path, monkeypatch
):
    monkeypatch.setattr(sweeper_module.settings.upload, "TMP_DIR", tmp_path)
    past = datetime.now().timestamp() - 10
    busy, busy_file = await session(store, tmp_path, past)
    renewed, renewed_file = await session(store, tmp_path, past)
    missing, _ = await session(store, tmp_path, past)
    await redis.delete(f"tcloud:upload_session:{missing}")
    saved = await store.get_session(renewed)
    await redis.set(
        f"tcloud:upload_session:{renewed}",
        encode({**saved, "expires_at": past + 3600}),
    )
    async with store.holding(f"upload:{busy}") as held:
        assert held
        assert await TaskSweeper(store).clean_tmp() == 0
    assert busy_file.exists() and renewed_file.exists()
    assert await store.get_session(busy) and await store.get_session(renewed)
    assert missing not in [item for item, _, _ in await store.sessions()]
    assert await TaskSweeper(store).clean_tmp() == 1
    assert not busy_file.exists() and await store.get_session(busy) is None


async def test_run_loop_survives_errors(store, monkeypatch):
    monkeypatch.setattr(sweeper_module.settings.upload, "SWEEP_INTERVAL", 0.01)
    sweeper = TaskSweeper(store)
    calls = []

    async def sweep():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("redis blip")
        return 0

    async def clean_tmp():
        return 0

    monkeypatch.setattr(sweeper, "sweep", sweep)
    monkeypatch.setattr(sweeper, "clean_tmp", clean_tmp)
    loop = asyncio.create_task(sweeper.run())
    while len(calls) < 3:
        await asyncio.sleep(0.01)
    assert not loop.done()
    loop.cancel()
