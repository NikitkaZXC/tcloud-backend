import asyncio
from types import SimpleNamespace
from uuid import uuid7

import pytest

from src.config import settings
from src.infrastructure.background.heartbeat import Heartbeat
from src.infrastructure.cache import TaskStore, tasks


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


async def queued(store, queue: str = "uploads.prepare") -> object:
    task_id = uuid7()
    await store.create(
        {
            "id": task_id,
            "status": "queued",
            "generation": 0,
            "waiting": {"reason": f"queue:{queue}"},
        }
    )
    return task_id


async def test_claim_once_per_queue_and_generation(store):
    task_id = await queued(store)
    assert not await store.claim(task_id, "uploads.prepare", 1, False)
    results = await asyncio.gather(
        *(store.claim(task_id, "uploads.prepare", 0, False) for _ in range(5))
    )
    assert sorted(results) == [False] * 4 + [True]
    task = await store.get(task_id)
    assert (task["status"], task["claimed"], task["waiting"]) == (
        "running",
        "uploads.prepare",
        None,
    )
    assert await store.claim(task_id, "uploads.prepare", 0, True)
    assert await store.claim(task_id, "uploads.telegram", 0, False)
    assert not await store.claim(uuid7(), "uploads.prepare", 0, False)


async def test_claim_refuses_finished_tasks(store):
    task_id = await queued(store)
    await store.update(task_id, status="cancelled")
    assert not await store.claim(task_id, "uploads.prepare", 0, True)


async def test_requeue_only_while_waiting_in_queue(store):
    task_id = await queued(store, "uploads.telegram")
    assert await store.requeue(task_id, "high", 9) == (1, "uploads.telegram")
    task = await store.get(task_id)
    assert (task["generation"], task["priority"], task["priority_level"]) == (
        1,
        "high",
        9,
    )
    assert not await store.claim(task_id, "uploads.telegram", 0, False)
    assert await store.claim(task_id, "uploads.telegram", 1, False)
    assert await store.requeue(task_id, "low", 1) is None
    assert await store.requeue(uuid7(), "low", 1) is None


async def test_cancel_queued_running_and_finished(store):
    waiting = await queued(store)
    assert await store.cancel(waiting) == "cancelled"
    task = await store.get(waiting)
    assert (task["status"], task["cancel"], task["error"]["code"]) == (
        "cancelled",
        True,
        "cancelled",
    )
    assert await store.cancel(waiting) == "finished"
    running = await queued(store)
    await store.claim(running, "uploads.prepare", 0, False)
    assert await store.cancel(running) == "running"
    assert (await store.get(running))["status"] == "running"
    assert await store.cancel(uuid7()) == "missing"


async def test_release_and_active_ids(store):
    task_id = uuid7()
    await store.activate(task_id)
    await store.lock_path("tcloud/x/a.jpg", task_id)
    assert await store.active_ids() == [str(task_id)]
    await store.release(task_id, "tcloud/x/a.jpg")
    assert await store.active_ids() == []
    assert not await store.path_locked("tcloud/x/a.jpg")


async def waiting_in(store, queue: str, level: int) -> str:
    task_id = await queued(store, queue)
    await store.join_queue(task_id, queue, level)
    return str(task_id)


async def test_positions_follow_priority_then_order(store):
    queue = "uploads.telegram"
    low = [await waiting_in(store, queue, 1) for _ in range(2)]
    normal = await waiting_in(store, queue, 5)
    high = await waiting_in(store, queue, 9)
    tasks = await store.get_many([*low, normal, high])
    assert await store.positions(tasks) == {
        high: 1,
        normal: 2,
        low[0]: 3,
        low[1]: 4,
    }


async def test_claim_and_release_leave_the_queue(store):
    queue = "uploads.prepare"
    first = await waiting_in(store, queue, 5)
    second = await waiting_in(store, queue, 5)
    third = await waiting_in(store, queue, 5)
    assert await store.claim(first, queue, 0, False)
    await store.release(second, "tcloud/x/none.jpg")
    tasks = await store.get_many([first, second, third])
    assert await store.positions(tasks) == {third: 1}


async def test_raised_priority_moves_to_front(store):
    queue = "uploads.telegram"
    ids = [await waiting_in(store, queue, 1) for _ in range(3)]
    generation, _ = await store.requeue(ids[2], "high", 9)
    await store.join_queue(ids[2], queue, 9)
    positions = await store.positions(await store.get_many(ids))
    assert positions == {ids[2]: 1, ids[0]: 2, ids[1]: 3} and generation == 1


async def flags(store, storage_id) -> dict[str, bool]:
    return (await store.activity([storage_id]))[storage_id]


async def test_activity_lasts_while_operation_runs(store, monkeypatch):
    monkeypatch.setattr("src.infrastructure.cache.tasks.ACTIVITY_TTL", 1)
    monkeypatch.setattr("src.infrastructure.cache.tasks.ACTIVITY_REFRESH", 0.2)
    monkeypatch.setattr("src.infrastructure.cache.tasks.ACTIVITY_TAIL", 0.3)
    storage_id = uuid7()
    assert await flags(store, storage_id) == {"read": False, "write": False}
    async with store.active(storage_id, "read"):
        await asyncio.sleep(1.5)
        assert await flags(store, storage_id) == {"read": True, "write": False}
    assert await flags(store, storage_id) == {"read": True, "write": False}
    await asyncio.sleep(0.4)
    assert await flags(store, storage_id) == {"read": False, "write": False}


async def test_activity_counts_overlapping_operations(store, monkeypatch):
    monkeypatch.setattr("src.infrastructure.cache.tasks.ACTIVITY_TAIL", 0.2)
    storage_id = uuid7()
    async with store.active(storage_id, "write"):
        async with store.active(storage_id, "write"):
            pass
        await asyncio.sleep(0.3)
        assert (await flags(store, storage_id))["write"]
    await asyncio.sleep(0.3)
    assert not (await flags(store, storage_id))["write"]


async def test_activity_ends_on_error_and_cancel(store, monkeypatch):
    monkeypatch.setattr("src.infrastructure.cache.tasks.ACTIVITY_TAIL", 0.2)
    storage_id = uuid7()
    with pytest.raises(RuntimeError):
        async with store.active(storage_id, "write"):
            raise RuntimeError
    started = asyncio.Event()

    async def hang() -> None:
        async with store.active(storage_id, "read"):
            started.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(hang())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    assert await flags(store, storage_id) == {"read": False, "write": False}


async def test_activity_of_crashed_operation_expires(store, monkeypatch):
    monkeypatch.setattr("src.infrastructure.cache.tasks.ACTIVITY_TTL", 0.5)
    storage_id = uuid7()
    await store._mark_activity(
        store._key("activity", storage_id, "read"), "crashed", 0.5
    )
    assert (await flags(store, storage_id))["read"]
    await asyncio.sleep(0.6)
    assert not (await flags(store, storage_id))["read"]


async def test_thumb_cache_ttl_and_memory_cap(store, redis, monkeypatch):
    monkeypatch.setattr(settings.redis, "THUMB_CACHE_MB", 1)
    clock = SimpleNamespace(time=lambda: 1000.0)
    monkeypatch.setattr(tasks, "time", clock)
    data = [bytes([index]) * 250_000 for index in range(4)]
    files = [uuid7() for _ in data]
    for file_id, body in zip(files[:3], data[:3]):
        assert await store.keep_thumb(file_id, 1, body)
    assert [await store.thumb(file_id, 1) for file_id in files[:3]] == data[:3]
    assert 0 < await redis.ttl(f"tcloud:thumb:{files[0]}:1") <= 180
    assert await store.keep_thumb(files[3], 1, data[3])
    assert await store.thumb(files[0], 1) is None
    assert await store.thumb(files[3], 1) == data[3]
    assert int(await redis.get("tcloud:thumbs:bytes")) == 3 * 333_336
    assert await redis.zcard("tcloud:thumbs") == 3
    assert await store.keep_thumb(files[3], 1, b"")
    assert int(await redis.get("tcloud:thumbs:bytes")) == 2 * 333_336
    clock.time = lambda: 1181.0
    assert await store.keep_thumb(files[0], 2, b"x")
    assert await redis.zcard("tcloud:thumbs") == 1
    assert int(await redis.get("tcloud:thumbs:bytes")) == 4
    assert not await store.keep_thumb(files[1], 2, bytes(800_000))
    monkeypatch.setattr(settings.redis, "THUMB_CACHE_MB", 0)
    assert not await store.keep_thumb(files[1], 3, b"x")


async def test_get_reads_only_asked_fields(store):
    task_id = uuid7()
    await store.create(
        {"id": task_id, "status": "running", "prepared": {"thumbnail": "x" * 1000}}
    )
    assert await store.get(task_id, ("status", "cancel")) == {
        "id": str(task_id),
        "status": "running",
    }
    assert (await store.get(task_id))["prepared"]
    assert await store.get(uuid7(), ("status",)) is None
    assert await store.get_many([str(uuid7()), str(task_id)], ("status",)) == [
        {"id": str(task_id), "status": "running"}
    ]


async def test_workers_come_from_the_registry_not_a_keyspace_scan(
    store, redis, monkeypatch
):
    monkeypatch.setattr(settings.upload, "HEARTBEAT_INTERVAL", 0.05)
    beat = Heartbeat()
    beat.start(["uploads.prepare"], "w1@host", lambda: True)
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while not await store.workers():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.02)
    finally:
        beat.stop()

    async def no_scan(*args, **kwargs):
        raise AssertionError("workers() must not SCAN")

    await redis.zadd("tcloud:workers", {"tcloud:worker:uploads.download:old": 1})
    await redis.set("tcloud:worker:uploads.telegram:w2", '{"at": 5, "telegram": null}')
    await redis.zadd("tcloud:workers", {"tcloud:worker:uploads.telegram:w2": 1e12})
    monkeypatch.setattr(redis, "scan_iter", no_scan)
    beats = await store.workers()
    assert beats == {"uploads.telegram": (5, None)}
    assert (
        await redis.zscore("tcloud:workers", "tcloud:worker:uploads.download:old")
        is None
    )


async def test_lua_runs_by_sha(store, redis):
    await redis.config_resetstat()
    task_id = uuid7()
    await store.create({"id": task_id, "status": "queued", "generation": 0})
    for _ in range(3):
        await store.cancel(task_id)
        await store.unlock_path("a", task_id)
    stats = await redis.info("commandstats")
    assert stats["cmdstat_evalsha"]["calls"] >= 6
    assert "cmdstat_eval" not in stats
