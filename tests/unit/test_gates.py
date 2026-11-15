import asyncio
import shutil
from collections import namedtuple

import pytest

from src.infrastructure.background import DiskBudget, Gate, InsufficientStorageError

Usage = namedtuple("Usage", "total used free")


async def test_gate_limits_and_orders():
    gate = Gate("test", 2)
    active, peak, order = 0, 0, []

    async def work(key):
        nonlocal active, peak
        async with gate.slot(key):
            active += 1
            peak = max(peak, active)
            order.append(key)
            await asyncio.sleep(0.01)
            active -= 1

    tasks = [asyncio.create_task(work(index)) for index in range(6)]
    await asyncio.sleep(0)
    assert [gate.position(index) for index in range(2, 6)] == [1, 2, 3, 4]
    await asyncio.gather(*tasks)
    assert peak == 2 and order == list(range(6)) and gate.position(0) is None


def test_disk_budget_counts_reserved_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(0, 0, 1000))
    budget = DiskBudget(tmp_path, min_free=100)
    first = budget.reserve(500)
    with pytest.raises(InsufficientStorageError):
        budget.reserve(500)
    first.update(400)
    assert budget.free() == 900
    first.release()
    budget.reserve(800)


def test_reservation_fails_when_disk_runs_out(tmp_path, monkeypatch):
    free = {"value": 1000}
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(0, 0, free["value"]))
    reservation = DiskBudget(tmp_path, min_free=100).reserve(0)
    free["value"] = 50
    with pytest.raises(InsufficientStorageError):
        reservation.update(10)
