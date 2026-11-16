import time
from collections import Counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import TaskNotFoundError
from src.application.schemas import (
    FileServiceRead,
    TaskListServiceRead,
    TaskServiceRead,
    TaskSummaryServiceRead,
    TaskWaitingServiceRead,
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
    def render(task: dict, file: FileServiceRead | None) -> TaskServiceRead:
        fields = {name: task.get(name) for name in TaskServiceRead.model_fields}
        fields.update(
            progress=None,
            speed=None,
            eta=None,
            waiting=None,
            file=file,
            cancel_requested=bool(task.get("cancel")),
        )
        view = TaskServiceRead(**fields)
        waiting = task.get("waiting")
        if waiting:
            until = waiting.get("until")
            view.waiting = TaskWaitingServiceRead(
                reason=waiting["reason"],
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
        views = []
        for task in tasks:
            found = files.get(UUID(task["file_id"])) if task.get("file_id") else None
            views.append(
                self.render(task, FileServiceRead.from_db(*found) if found else None)
            )
        return views

    async def get(self, task_id: UUID) -> TaskServiceRead:
        task = await self._store.get(task_id)
        if not task:
            raise TaskNotFoundError("task not found")
        return (await self.render_many([task]))[0]

    async def get_batch(self, batch_id: UUID) -> TaskListServiceRead:
        views = await self.render_many(
            await self._store.get_many(await self._store.batch(batch_id))
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
