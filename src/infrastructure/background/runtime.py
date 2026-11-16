import asyncio
from collections.abc import Coroutine

from src.config import settings
from src.infrastructure.background.disk import DiskBudget, MiB
from src.infrastructure.cache import TaskStore


class AppRuntime:
    def __init__(
        self, telegram, scanner, store: TaskStore, make_telegram, make_login
    ) -> None:
        self.telegram = telegram
        self.make_telegram = make_telegram
        self.make_login = make_login
        self.logins: dict = {}
        self.auth_lock = asyncio.Lock()
        self.scanner = scanner
        self.store = store
        self.disk = DiskBudget(
            settings.upload.TMP_DIR, settings.upload.MIN_FREE_DISK_MB * MiB
        )
        self.premium: tuple[float, bool] | None = None
        self._tasks: set[asyncio.Task] = set()
        self.handoff_lock = asyncio.Lock()

    def spawn(self, coroutine: Coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
