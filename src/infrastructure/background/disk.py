import shutil
from pathlib import Path

from src.infrastructure.background.exception import InsufficientStorageError

MiB = 1024 * 1024


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
