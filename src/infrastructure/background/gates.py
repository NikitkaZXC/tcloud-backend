import asyncio
import shutil
from collections.abc import AsyncIterator, Hashable
from contextlib import asynccontextmanager
from pathlib import Path

from src.infrastructure.background.exception import InsufficientStorageError

MiB = 1024 * 1024


class Gate:
    def __init__(self, name: str, size: int) -> None:
        self.name = name
        self._semaphore = asyncio.Semaphore(size)
        self._waiting: list[Hashable] = []
        self.active = 0

    @asynccontextmanager
    async def slot(self, key: Hashable) -> AsyncIterator[None]:
        self._waiting.append(key)
        try:
            await self._semaphore.acquire()
        finally:
            self._waiting.remove(key)
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
            self._semaphore.release()

    def position(self, key: Hashable) -> int | None:
        try:
            return self._waiting.index(key) + 1
        except ValueError:
            return None


class Reservation:
    def __init__(self, budget: "DiskBudget", size: int) -> None:
        self.budget, self.size, self.written = budget, size, 0

    def update(self, written: int, total: int | None = None) -> None:
        self.written = written
        self.size = max(self.size, total or 0, written)
        self.budget.check(0)

    def release(self) -> None:
        self.budget.items.discard(self)


class DiskBudget:
    def __init__(self, path: Path, min_free: int) -> None:
        self.path, self.min_free = path, min_free
        self.items: set[Reservation] = set()

    def free(self) -> int:
        pending = sum(item.size - item.written for item in self.items)
        return shutil.disk_usage(self.path).free - pending

    def check(self, size: int) -> None:
        if self.free() - size < self.min_free:
            raise InsufficientStorageError(
                f"not enough disk space for temporary files (need {size // MiB} MiB more)"
            )

    def reserve(self, size: int) -> Reservation:
        self.check(size)
        reservation = Reservation(self, size)
        self.items.add(reservation)
        return reservation
