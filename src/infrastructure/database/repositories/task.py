from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import TaskDB
from src.infrastructure.enums import TaskStatus


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, task_id: UUID) -> TaskDB | None:
        return await self._session.get(TaskDB, task_id, populate_existing=True)

    async def create(self, task: TaskDB) -> TaskDB:
        self._session.add(task)
        await self._session.flush()
        return task

    async def set_values(self, task_id: UUID, values: dict) -> None:
        await self._session.execute(
            update(TaskDB).where(TaskDB.id == task_id).values(**values)
        )

    async def claim(self, task_id: UUID) -> bool:
        statement = (
            update(TaskDB)
            .where(TaskDB.id == task_id, TaskDB.status == TaskStatus.PENDING)
            .values(status=TaskStatus.RUNNING)
            .returning(TaskDB.id)
        )
        return bool(await self._session.scalar(statement))

    async def fail_active(self, error: dict) -> None:
        statement = (
            update(TaskDB)
            .where(TaskDB.status.in_(TaskStatus.active()))
            .values(status=TaskStatus.FAILED, error=error)
        )
        await self._session.execute(statement)

    async def expire_pending(self, ttl_seconds: int, error: dict) -> None:
        statement = (
            update(TaskDB)
            .where(
                TaskDB.status == TaskStatus.PENDING,
                TaskDB.created_at < func.now() - timedelta(seconds=ttl_seconds),
            )
            .values(status=TaskStatus.FAILED, error=error)
        )
        await self._session.execute(statement)

    async def count_active(self) -> int:
        statement = (
            select(func.count())
            .select_from(TaskDB)
            .where(TaskDB.status.in_(TaskStatus.active()))
        )
        return await self._session.scalar(statement) or 0

    async def active_path_exists(self, path: str) -> bool:
        statement = select(TaskDB.id).where(
            TaskDB.path == path, TaskDB.status.in_(TaskStatus.active())
        )
        return bool(await self._session.scalar(statement))

    async def get_page(
        self, batch_id: UUID | None, status: str | None, limit: int, offset: int
    ) -> list[TaskDB]:
        statement = (
            select(TaskDB)
            .where(*self._filters(batch_id, status))
            .order_by(TaskDB.created_at.desc(), TaskDB.id)
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.scalars(statement)).all())

    async def count_by_status(
        self, batch_id: UUID | None, status: str | None
    ) -> dict[str, int]:
        statement = (
            select(TaskDB.status, func.count())
            .where(*self._filters(batch_id, status))
            .group_by(TaskDB.status)
        )
        return {
            row[0]: row[1] for row in (await self._session.execute(statement)).all()
        }

    async def get_batch(self, batch_id: UUID) -> list[TaskDB]:
        statement = select(TaskDB).where(TaskDB.batch_id == batch_id)
        return list((await self._session.scalars(statement)).all())

    @staticmethod
    def _filters(batch_id: UUID | None, status: str | None) -> list:
        filters = []
        if batch_id:
            filters.append(TaskDB.batch_id == batch_id)
        if status:
            filters.append(TaskDB.status == status)
        return filters
