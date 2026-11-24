import asyncio
import shutil
from collections import namedtuple
from uuid import uuid7

import pytest

from src.infrastructure.background import DiskBudget, InsufficientStorageError
from src.infrastructure.background import disk as disk_module
from src.infrastructure.cache import TaskStore

Usage = namedtuple("Usage", "total used free")


@pytest.fixture
def free(monkeypatch) -> dict:
    value = {"value": 1000}
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(0, 0, value["value"]))
    monkeypatch.setattr(disk_module, "SYNC_INTERVAL", 0.01)
    return value


@pytest.fixture
def store(redis) -> TaskStore:
    return TaskStore(redis)


def budget(store, tmp_path, min_free: int = 0) -> DiskBudget:
    return DiskBudget(tmp_path, min_free, store)


async def synced() -> None:
    await asyncio.sleep(0.05)


async def test_api_sees_worker_ciphertext(store, tmp_path, free):
    worker, api = budget(store, tmp_path), budget(store, tmp_path)
    reservation = await worker.reserve(uuid7(), 600)
    with pytest.raises(InsufficientStorageError):
        await api.check(500)
    await api.check(400)
    await reservation.release()
    await api.check(1000)


async def test_concurrent_reserves_of_two_processes_fit_once(store, tmp_path, free):
    first, second = budget(store, tmp_path), budget(store, tmp_path)

    async def post(process: DiskBudget):
        async with store.disk_lock():
            return await process.reserve(uuid7(), 600)

    results = await asyncio.gather(post(first), post(second), return_exceptions=True)
    assert sorted(type(result).__name__ for result in results) == [
        "InsufficientStorageError",
        "Reservation",
    ]
    for result in results:
        if not isinstance(result, Exception):
            await result.release()


async def test_queued_hold_blocks_others_but_not_itself(store, tmp_path, free):
    api, worker = budget(store, tmp_path), budget(store, tmp_path)
    task_id = uuid7()
    await api.hold(task_id, 700)
    with pytest.raises(InsufficientStorageError):
        await api.check(400)
    reservation = await worker.reserve(task_id, 700)
    assert await store.reserved_bytes() == 700
    await reservation.release()
    assert await store.reserved_bytes() == 0


async def test_release_keeps_bytes_for_the_next_stage(store, tmp_path, free):
    process = budget(store, tmp_path)
    task_id = uuid7()
    reservation = await process.reserve(task_id, 800)
    await reservation.release(400)
    assert await store.reserved_bytes() == 400
    await store.release(task_id, "tcloud/x/a.bin")
    assert await store.reserved_bytes() == 0


async def test_redelivery_reserves_once_per_owner(store, tmp_path, free):
    first, second = budget(store, tmp_path), budget(store, tmp_path)
    task_id = uuid7()
    await first.reserve(task_id, 600)
    again = await second.reserve(task_id, 600)
    assert await store.reserved_bytes() == 600
    await again.release()
    assert await store.reserved_bytes() == 0


async def test_expired_holder_drops_out(store, tmp_path, free):
    await store.reserve_disk("crashed", 900, 0.01)
    await asyncio.sleep(0.02)
    assert await store.reserved_bytes() == 0
    assert not await store._redis.hlen(store._key("disk", "reserved"))


async def test_half_written_download_is_not_counted_twice(store, tmp_path, free):
    worker, api = budget(store, tmp_path), budget(store, tmp_path)
    reservation = await worker.reserve(uuid7(), 0)
    reservation.update(0, 1000)
    await synced()
    assert await api.pending() == 1000
    reservation.update(400)
    free["value"] = 600
    await synced()
    assert await api.pending() == 600
    await api.check(0)
    reservation.update(400, 1000)
    await reservation.release()


async def test_growing_reservation_fails_when_disk_runs_out(store, tmp_path, free):
    reservation = await budget(store, tmp_path, 100).reserve(uuid7(), 0)
    free["value"] = 50
    with pytest.raises(InsufficientStorageError):
        reservation.update(10)
    await reservation.release()


async def test_reservation_sees_other_holders_while_writing(store, tmp_path, free):
    reservation = await budget(store, tmp_path).reserve(uuid7(), 0)
    await store.reserve_disk("other", 2000, 60)
    await synced()
    with pytest.raises(InsufficientStorageError):
        reservation.update(20)
    await reservation.release()


async def test_exclude_skips_own_entry(store, tmp_path, free):
    process = budget(store, tmp_path)
    await store.reserve_disk("other", 300, 60)
    with pytest.raises(InsufficientStorageError):
        await process.check(800)
    await process.check(800, exclude="other")
