import asyncio
import time
from datetime import datetime

from loguru import logger

from src.application.services.temp import remove_task_files
from src.config import settings
from src.infrastructure.background import queue_sizes
from src.infrastructure.cache import TaskStore
from src.infrastructure.enums import TaskStatus

LOST = {"code": "lost", "message": "the queue lost this task, upload the file again"}


class TaskSweeper:
    def __init__(self, store: TaskStore) -> None:
        self._store = store
        self._suspects: set[str] = set()

    @staticmethod
    def _queue(task: dict) -> str | None:
        reason = (task.get("waiting") or {}).get("reason", "")
        if reason.startswith("queue:"):
            return reason.removeprefix("queue:")
        return task.get("claimed")

    @staticmethod
    def _stale(task: dict) -> bool:
        seen = max(
            task.get("beat_at") or 0,
            datetime.fromisoformat(task["updated_at"]).timestamp(),
        )
        return time.time() - seen > settings.upload.STALE_AFTER

    async def sweep(self) -> int:
        sizes = await queue_sizes()
        if sizes is None:
            return 0
        ids = await self._store.active_ids()
        tasks = {task["id"]: task for task in await self._store.get_many(ids)}
        suspects, lost = set(), 0
        for task_id in ids:
            task = tasks.get(task_id)
            if not task or task["status"] in TaskStatus.finished():
                if task:
                    await self._store.release(task_id, task["path"])
                else:
                    await self._store.deactivate(task_id)
                continue
            if not self._stale(task) or sizes.get(self._queue(task), 1):
                continue
            if task_id not in self._suspects:
                suspects.add(task_id)
                continue
            logger.warning("Task {} lost by the queue: {}", task_id, task["path"])
            await self._store.update(
                task_id, status=TaskStatus.FAILED, error=LOST, waiting=None
            )
            await self._store.release(task_id, task["path"])
            remove_task_files(task)
            lost += 1
        self._suspects = suspects
        return lost

    async def run(self) -> None:
        while True:
            await asyncio.sleep(settings.upload.SWEEP_INTERVAL)
            try:
                await self.sweep()
            except Exception:
                logger.exception("Task sweep failed")
