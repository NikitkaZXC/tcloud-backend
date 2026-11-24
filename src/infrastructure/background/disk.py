import asyncio
import shutil
import time
from pathlib import Path
from uuid import UUID

from src.infrastructure.background.exception import InsufficientStorageError

MiB = 1024 * 1024
SESSIONS_REFRESH = 1.0


class Reservation:
    def __init__(
        self, budget: "DiskBudget", size: int, exclude: UUID | None, sessions: int
    ) -> None:
        self.budget, self.size, self.written = budget, size, 0
        self.exclude, self.sessions = exclude, sessions
        self.read_at = time.monotonic()
        self.refresh: asyncio.Task | None = None

    def update(self, written: int, total: int | None = None) -> None:
        self.written = written
        self.size = max(self.size, total or 0, written)
        if time.monotonic() - self.read_at > SESSIONS_REFRESH and (
            self.refresh is None or self.refresh.done()
        ):
            self.refresh = asyncio.create_task(self._reload())
        self.budget.require(0, self.sessions)

    async def _reload(self) -> None:
        self.read_at = time.monotonic()
        self.sessions = await self.budget.sessions(self.exclude)

    def release(self) -> None:
        self.budget.items.discard(self)


class DiskBudget:
    def __init__(self, path: Path, min_free: int, store=None) -> None:
        self.path, self.min_free, self.store = path, min_free, store
        self.items: set[Reservation] = set()

    def free(self) -> int:
        pending = sum(item.size - item.written for item in self.items)
        return shutil.disk_usage(self.path).free - pending

    async def sessions(self, exclude: UUID | None = None) -> int:
        return await self.store.session_bytes(exclude) if self.store else 0

    def require(self, size: int, sessions: int) -> None:
        if self.free() - sessions - size < self.min_free:
            raise InsufficientStorageError(
                f"not enough disk space for temporary files (need {size // MiB} MiB more)"
            )

    async def check(self, size: int, exclude: UUID | None = None) -> int:
        sessions = await self.sessions(exclude)
        self.require(size, sessions)
        return sessions

    async def reserve(self, size: int, exclude: UUID | None = None) -> Reservation:
        sessions = await self.check(size, exclude)
        reservation = Reservation(self, size, exclude, sessions)
        self.items.add(reservation)
        return reservation
