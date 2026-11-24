import asyncio
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.application.services.temp import remove_task_files, task_files
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

    async def clean_tmp(self) -> int:
        tasks = await self._store.get_many(await self._store.active_ids())
        used = {path for task in tasks for path in task_files(task)}
        now = time.time()
        old = now - settings.upload.ORPHAN_AGE
        removed = 0
        for archive, until in (await self._store.archives()).items():
            if until > now:
                used.add(Path(archive))
                continue
            Path(archive).unlink(missing_ok=True)
            await self._store.forget_archive(archive)
            removed += 1
        for session_id, until, session in await self._store.sessions():
            if session and until > now:
                used.add(Path(session["task"]["params"]["file"]))
                continue
            async with self._store.holding(f"upload:{session_id}") as held:
                session = await self._store.get_session(session_id)
                if not held or (session and session["expires_at"] > now):
                    used.update(task_files(session["task"]) if session else ())
                    continue
                await self._store.drop_session(session_id)
                if session:
                    task = session["task"]
                    Path(task["params"]["file"]).unlink(missing_ok=True)
                    await self._store.release(task["id"], task["path"])
                    logger.info(
                        "Upload session {} expired: {}", session_id, task["path"]
                    )
                    removed += 1
        for item in settings.upload.TMP_DIR.iterdir():
            try:
                if item not in used and item.stat().st_mtime < old:
                    item.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
        if removed:
            logger.info("{} orphaned temporary files removed", removed)
        return removed

    async def run(self) -> None:
        while True:
            await asyncio.sleep(settings.upload.SWEEP_INTERVAL)
            try:
                await self.sweep()
                await self.clean_tmp()
            except Exception:
                logger.exception("Task sweep failed")
