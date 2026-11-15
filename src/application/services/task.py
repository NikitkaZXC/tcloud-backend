import time
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
from src.infrastructure.background import BackgroundRuntime
from src.infrastructure.database.models import TaskDB
from src.infrastructure.database.repositories import FileRepository, TaskRepository
from src.infrastructure.enums import TaskStatus

FINISHED = (TaskStatus.DONE, TaskStatus.FAILED)


class TaskService:
    def __init__(self, session: AsyncSession, runtime: BackgroundRuntime) -> None:
        self._session = session
        self._runtime = runtime
        self._repo = TaskRepository(session)
        self._files = FileRepository(session)

    def render(self, task: TaskDB, file: FileServiceRead | None) -> TaskServiceRead:
        view = TaskServiceRead(
            id=task.id,
            batch_id=task.batch_id,
            status=task.status,
            stage=task.stage,
            path=task.path,
            bytes_done=task.bytes_done,
            bytes_total=task.bytes_total,
            progress=None,
            speed=None,
            eta=None,
            queue_position=None,
            waiting=None,
            error=task.error,
            warnings=task.warnings,
            file=file,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
        live = self._runtime.live.get(task.id)
        if live and task.status == TaskStatus.RUNNING:
            view.stage, view.bytes_done, view.bytes_total = (
                live.stage,
                live.bytes_done,
                live.bytes_total,
            )
            if live.gate:
                view.queue_position = live.gate.position(task.id)
                view.waiting = TaskWaitingServiceRead(reason=f"queue:{live.gate.name}")
            elif live.waiting_reason:
                seconds = max(0, round((live.waiting_until or 0) - time.time()))
                view.waiting = TaskWaitingServiceRead(
                    reason=live.waiting_reason, seconds=seconds
                )
            elapsed = time.monotonic() - live.started
            if elapsed > 0 and live.bytes_done and not view.waiting:
                view.speed = round(live.bytes_done / elapsed)
                if view.bytes_total:
                    view.eta = round(
                        (view.bytes_total - live.bytes_done) / view.speed, 1
                    )
        if task.status == TaskStatus.DONE:
            view.progress = 100.0
        elif view.bytes_total:
            view.progress = round(100 * view.bytes_done / view.bytes_total, 1)
        return view

    async def render_many(self, tasks: list[TaskDB]) -> list[TaskServiceRead]:
        files = await self._files.get_many_with_chat(
            [task.file_id for task in tasks if task.file_id]
        )
        views = []
        for task in tasks:
            found = files.get(task.file_id)
            views.append(
                self.render(task, FileServiceRead.from_db(*found) if found else None)
            )
        return views

    async def get(self, task_id: UUID) -> TaskServiceRead:
        task = await self._repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError("task not found")
        return (await self.render_many([task]))[0]

    async def get_page(
        self, batch_id: UUID | None, status: str | None, limit: int, offset: int
    ) -> TaskListServiceRead:
        tasks = await self._repo.get_page(batch_id, status, limit, offset)
        counts = await self._repo.count_by_status(batch_id, status)
        summary = TaskSummaryServiceRead(total=sum(counts.values()), by_status=counts)
        if batch_id:
            views = [
                self.render(task, None) for task in await self._repo.get_batch(batch_id)
            ]
            summary.bytes_done = sum(view.bytes_done or 0 for view in views)
            summary.bytes_total = sum(view.bytes_total or 0 for view in views)
            summary.finished = sum(view.status in FINISHED for view in views)
            summary.progress = (
                round(100 * summary.finished / len(views), 1) if views else None
            )
        return TaskListServiceRead(items=await self.render_many(tasks), summary=summary)
