import asyncio
import shutil
from pathlib import Path
from uuid import UUID

from src.config import settings
from src.infrastructure.background.exception import InsufficientStorageError

MiB = 1024 * 1024
SYNC_INTERVAL = 1.0
RESERVATION_TTL = 30


class Reservation:
    def __init__(
        self, budget: "DiskBudget", owner: str, size: int, pending: int
    ) -> None:
        self.budget, self.owner, self.size, self.written = budget, owner, size, 0
        self.pending = pending
        self.keep = asyncio.create_task(self._keep())

    def remaining(self) -> int:
        return max(self.size - self.written, 0)

    def update(self, written: int, total: int | None = None) -> None:
        self.written = written
        self.size = max(self.size, total or 0, written)
        self.budget.require(self.remaining(), self.pending)

    async def _keep(self) -> None:
        while True:
            await asyncio.sleep(SYNC_INTERVAL)
            await self.budget.store.reserve_disk(
                self.owner, self.remaining(), RESERVATION_TTL
            )
            self.pending = await self.budget.pending(self.owner)

    async def release(self, keep: int = 0) -> None:
        self.keep.cancel()
        await asyncio.gather(self.keep, return_exceptions=True)
        await asyncio.shield(self.budget.park(self.owner, keep))


class DiskBudget:
    def __init__(self, path: Path, min_free: int, store=None) -> None:
        self.path, self.min_free, self.store = path, min_free, store

    async def pending(self, exclude: UUID | str | None = None) -> int:
        return await self.store.reserved_bytes(
            exclude
        ) + await self.store.session_bytes(exclude)

    def require(self, size: int, pending: int) -> None:
        if shutil.disk_usage(self.path).free - pending - size < self.min_free:
            raise InsufficientStorageError(
                f"not enough disk space for temporary files (need {size // MiB} MiB more)"
            )

    async def check(self, size: int, exclude: UUID | str | None = None) -> int:
        pending = await self.pending(exclude)
        self.require(size, pending)
        return pending

    async def park(self, owner: UUID | str, size: int) -> None:
        if size:
            await self.store.reserve_disk(str(owner), size, settings.upload.LOCK_TTL)
        else:
            await self.store.drop_disk(str(owner))

    async def hold(self, owner: UUID | str, size: int) -> None:
        await self.check(size, owner)
        await self.park(owner, size)

    async def reserve(self, owner: UUID | str, size: int) -> Reservation:
        pending = await self.check(size, owner)
        await self.store.reserve_disk(str(owner), size, RESERVATION_TTL)
        return Reservation(self, str(owner), size, pending)
