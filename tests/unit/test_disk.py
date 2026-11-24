import shutil
from collections import namedtuple

import pytest

from src.infrastructure.background import DiskBudget, InsufficientStorageError
from src.infrastructure.background import disk as disk_module

Usage = namedtuple("Usage", "total used free")


class Sessions:
    def __init__(self, sizes: dict[str, int]) -> None:
        self.sizes = sizes

    async def session_bytes(self, exclude=None) -> int:
        return sum(size for key, size in self.sizes.items() if key != str(exclude))


async def test_disk_budget_counts_reserved_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(0, 0, 1000))
    budget = DiskBudget(tmp_path, min_free=100)
    first = await budget.reserve(500)
    with pytest.raises(InsufficientStorageError):
        await budget.reserve(500)
    first.update(400)
    assert budget.free() == 900
    first.release()
    await budget.reserve(800)


async def test_reservation_fails_when_disk_runs_out(tmp_path, monkeypatch):
    free = {"value": 1000}
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(0, 0, free["value"]))
    reservation = await DiskBudget(tmp_path, min_free=100).reserve(0)
    free["value"] = 50
    with pytest.raises(InsufficientStorageError):
        reservation.update(10)


async def test_disk_budget_subtracts_sessions_except_own(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(0, 0, 1000))
    budget = DiskBudget(tmp_path, 0, Sessions({"own": 600, "other": 300}))
    with pytest.raises(InsufficientStorageError):
        await budget.check(200)
    await budget.reserve(600, exclude="own")
    with pytest.raises(InsufficientStorageError):
        await budget.reserve(200, exclude="own")


async def test_reservation_rereads_sessions_while_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(0, 0, 1000))
    monkeypatch.setattr(disk_module, "SESSIONS_REFRESH", 0)
    sessions = Sessions({"other": 0})
    reservation = await DiskBudget(tmp_path, 0, sessions).reserve(0)
    sessions.sizes["other"] = 2000
    reservation.update(10)
    await reservation.refresh
    with pytest.raises(InsufficientStorageError):
        reservation.update(20)
