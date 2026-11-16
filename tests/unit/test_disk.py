import shutil
from collections import namedtuple

import pytest

from src.infrastructure.background import DiskBudget, InsufficientStorageError

Usage = namedtuple("Usage", "total used free")


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
