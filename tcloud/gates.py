"""Ограничители для сотен одновременных загрузок: слоты на стадии и резерв места на диске."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Hashable
from contextlib import asynccontextmanager
from pathlib import Path

from .errors import TCloudError

MiB = 1024 * 1024


class Gate:
    """Не больше N задач на стадии одновременно; остальные ждут по порядку и видят свой номер."""

    def __init__(self, name: str, size: int):
        self.name = name
        self._sem = asyncio.Semaphore(size)  # очередь ожидающих в asyncio.Semaphore — FIFO
        self._waiting: list[Hashable] = []
        self.active = 0

    @asynccontextmanager
    async def slot(self, key: Hashable) -> AsyncIterator[None]:
        self._waiting.append(key)
        try:
            await self._sem.acquire()
        finally:
            self._waiting.remove(key)
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
            self._sem.release()

    def position(self, key: Hashable) -> int | None:
        try:
            return self._waiting.index(key) + 1
        except ValueError:
            return None


class Reservation:
    def __init__(self, budget: DiskBudget, size: int):
        self.budget, self.size, self.written = budget, size, 0

    def update(self, written: int, total: int | None = None) -> None:
        """Вызывается на каждый записанный кусок: место могло кончиться, пока файл шёл."""
        self.written = written
        self.size = max(self.size, total or 0, written)
        self.budget.check(0)

    def release(self) -> None:
        self.budget._items.discard(self)


class DiskBudget:
    """Резерв места во временной папке: принятые, но ещё не записанные байты считаются занятыми."""

    def __init__(self, path: Path, min_free: int):
        self.path, self.min_free = path, min_free
        self._items: set[Reservation] = set()

    def free(self) -> int:
        pending = sum(r.size - r.written for r in self._items)
        return shutil.disk_usage(self.path).free - pending

    def check(self, size: int) -> None:
        if self.free() - size < self.min_free:
            raise TCloudError(
                507, "insufficient_storage",
                f"not enough disk space for temporary files (need {size // MiB} MiB more)",
            )

    def reserve(self, size: int) -> Reservation:
        self.check(size)
        r = Reservation(self, size)
        self._items.add(r)
        return r
