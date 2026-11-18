import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    ArchiveExpiredError,
    ArchiveNotReadyError,
    TaskNotFoundError,
)
from src.application.schemas import (
    Actor,
    FileServiceRead,
    TaskListServiceRead,
    TaskServiceRead,
    TaskSummaryServiceRead,
    TaskWaitingServiceRead,
    TaskWarningServiceRead,
    task_warning,
)
from src.infrastructure.cache import TaskStore
from src.infrastructure.database.repositories import FileRepository
from src.infrastructure.enums import TaskStatus


class TaskService:
    def __init__(self, session: AsyncSession, store: TaskStore) -> None:
        self._session = session
        self._store = store
        self._files = FileRepository(session)

    @staticmethod
    def render(
        task: dict, file: FileServiceRead | None, position: int | None = None
    ) -> TaskServiceRead:
        fields = {name: task.get(name) for name in TaskServiceRead.model_fields}
        fields.update(
            progress=None,
            speed=None,
            eta=None,
            waiting=None,
            warnings=[],
            warning_details=[],
            file=file,
            queue_position=position,
            cancel_requested=bool(task.get("cancel")),
        )
        view = TaskServiceRead(**fields)
        for warning in task.get("warnings") or []:
            if isinstance(warning, str):
                warning = task_warning("other", warning)
            view.warnings.append(warning["message"])
            view.warning_details.append(TaskWarningServiceRead(**warning))
        waiting = task.get("waiting")
        if waiting:
            until = waiting.get("until")
            code, _, queue = waiting["reason"].partition(":")
            view.waiting = TaskWaitingServiceRead(
                reason=waiting["reason"],
                code=code,
                params={"queue": queue} if queue else {},
                seconds=max(0, round(until - time.time())) if until else None,
            )
        if task["status"] == TaskStatus.DONE:
            view.progress = 100.0
        elif view.bytes_total:
            view.progress = round(100 * view.bytes_done / view.bytes_total, 1)
        elapsed = time.time() - (task.get("stage_started") or time.time())
        if task["status"] == TaskStatus.RUNNING and not view.waiting and elapsed > 0:
            if view.bytes_done:
                view.speed = round(view.bytes_done / elapsed)
                if view.bytes_total:
                    view.eta = round(
                        (view.bytes_total - view.bytes_done) / view.speed, 1
                    )
        return view

    async def render_many(self, tasks: list[dict]) -> list[TaskServiceRead]:
        files = await self._files.get_many_with_chat(
            [UUID(task["file_id"]) for task in tasks if task.get("file_id")]
        )
        positions = await self._store.positions(tasks)
        views = []
        for task in tasks:
            found = files.get(UUID(task["file_id"])) if task.get("file_id") else None
            views.append(
                self.render(
                    task,
                    FileServiceRead.from_db(*found) if found else None,
                    positions.get(str(task["id"])),
                )
            )
        return views

    @staticmethod
    def visible(task: dict, actor: Actor | None) -> bool:
        return (
            actor is None or actor.is_admin or task.get("user_id") == str(actor.user_id)
        )

    async def get_raw(self, task_id: UUID, actor: Actor | None) -> dict:
        task = await self._store.get(task_id)
        if not task or not self.visible(task, actor):
            raise TaskNotFoundError("task not found")
        return task

    async def open_archive(self, task_id: UUID, actor: Actor) -> dict:
        task = await self._store.get(task_id)
        if (
            not task
            or not task.get("archive")
            or task.get("user_id") != str(actor.user_id)
        ):
            raise TaskNotFoundError("task not found")
        if task["status"] != TaskStatus.DONE:
            raise ArchiveNotReadyError(f"archive task is {task['status']}")
        archive = task["archive"]
        expired = (
            datetime.fromisoformat(archive["expires_at"]).timestamp() < time.time()
        )
        if expired or not Path(archive["path"]).is_file():
            raise ArchiveExpiredError(
                "archive was removed from the server, build it again"
            )
        return archive

    async def get(self, task_id: UUID, actor: Actor | None) -> TaskServiceRead:
        return (await self.render_many([await self.get_raw(task_id, actor)]))[0]

    async def get_batch(self, actor: Actor, batch_id: UUID) -> TaskListServiceRead:
        tasks = await self._store.get_many(await self._store.batch(batch_id))
        views = await self.render_many(
            [task for task in tasks if self.visible(task, actor)]
        )
        finished = sum(view.status in TaskStatus.finished() for view in views)
        summary = TaskSummaryServiceRead(
            total=len(views),
            by_status=dict(Counter(view.status for view in views)),
            bytes_done=sum(view.bytes_done for view in views),
            bytes_total=sum(view.bytes_total or 0 for view in views),
            finished=finished,
            progress=round(100 * finished / len(views), 1) if views else None,
        )
        return TaskListServiceRead(items=views, summary=summary)
