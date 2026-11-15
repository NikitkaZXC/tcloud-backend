import asyncio
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from uuid import UUID

from src.config import settings
from src.infrastructure.background.gates import DiskBudget, Gate, MiB
from src.infrastructure.enums import TaskStage


@dataclass
class LiveTask:
    stage: TaskStage
    bytes_total: int | None
    callback_url: str | None
    bytes_done: int = 0
    started: float = field(default_factory=time.monotonic)
    hook_at: float = 0.0
    gate: Gate | None = None
    waiting_reason: str | None = None
    waiting_until: float | None = None


class BackgroundRuntime:
    def __init__(self, telegram, scanner) -> None:
        self.telegram = telegram
        self.scanner = scanner
        self.downloads = Gate("download", settings.upload.DOWNLOAD_CONCURRENCY)
        self.scans = Gate("scanning", settings.upload.SCAN_CONCURRENCY)
        self.uploads = Gate("uploading_tg", settings.telegram.CONCURRENCY)
        self.disk = DiskBudget(
            settings.upload.TMP_DIR, settings.upload.MIN_FREE_DISK_MB * MiB
        )
        self.live: dict[UUID, LiveTask] = {}
        self.telegram_resume_at = 0.0
        self.premium: tuple[float, bool] | None = None
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coroutine: Coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
