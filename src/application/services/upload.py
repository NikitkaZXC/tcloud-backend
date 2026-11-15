from collections.abc import AsyncIterator
from uuid import UUID, uuid7

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    FileTooLargeError,
    PathConflictError,
    SizeMismatchError,
    TaskNotFoundError,
    TaskNotPendingError,
    TooManyTasksError,
    UploadInProgressError,
)
from src.application.schemas import (
    BatchItemServiceRead,
    BatchServiceRead,
    TaskServiceRead,
    UploadServiceCreate,
)
from src.application.services.account import AccountService
from src.application.services.pipeline import UploadPipeline
from src.application.services.storage import StorageService
from src.application.services.task import TaskService
from src.application.services.temp import temp_path, write_body
from src.config import settings
from src.infrastructure.background import BackgroundRuntime
from src.infrastructure.database.models import TaskDB
from src.infrastructure.database.repositories import FileRepository, TaskRepository
from src.infrastructure.enums import TaskSource, TaskStage, TaskStatus
from src.infrastructure.integrations.web import check_scheme, download
from src.infrastructure.media import check_name
from src.utilities.errors import TCloudError
from src.utilities.parsers import check_segment, join_path, split_file

EXPIRED = {"code": "expired", "message": "file was not sent in time"}


class UploadService:
    def __init__(
        self,
        session: AsyncSession,
        runtime: BackgroundRuntime,
        account_service: AccountService,
        storage_service: StorageService,
        task_service: TaskService,
    ) -> None:
        self._session = session
        self._runtime = runtime
        self._account = account_service
        self._storages = storage_service
        self._tasks = task_service
        self._repo = TaskRepository(session)
        self._files = FileRepository(session)
        self._pipeline = UploadPipeline(runtime)

    async def _taken(self, storage_id: UUID, folder: str, name: str) -> bool:
        if await self._files.get_by_path(storage_id, folder, name):
            return True
        return await self._repo.active_path_exists(join_path(storage_id, folder, name))

    async def _free_name(self, storage_id: UUID, folder: str, name: str) -> str:
        stem, dot, suffix = name.rpartition(".")
        for index in range(1, 10_000):
            candidate = f"{stem} ({index}){dot}{suffix}"
            if not await self._taken(storage_id, folder, candidate):
                check_segment(candidate)
                return candidate
        raise PathConflictError("no free name left")

    async def create_task_db(
        self, data: UploadServiceCreate, batch_id: UUID | None = None
    ) -> TaskDB:
        storage_id, folder, name = split_file(data.path)
        check_name(name)
        if data.callback_url:
            check_scheme(data.callback_url)
        if data.url:
            check_scheme(data.url)
        limit = await self._account.file_limit()
        if data.size is not None and data.size > limit:
            raise FileTooLargeError(
                f"file is {data.size} bytes, account limit is {limit}", limit=limit
            )
        if data.size is not None:
            self._runtime.disk.check(data.size)
        await self._repo.expire_pending(settings.upload.PENDING_TTL, EXPIRED)
        active = await self._repo.count_active()
        if active >= settings.upload.MAX_ACTIVE_TASKS:
            raise TooManyTasksError(
                f"{active} uploads are already in progress, try later", retry_after=30
            )
        await self._storages.get_db(storage_id)
        existing = await self._files.get_by_path(storage_id, folder, name)
        if existing and data.on_conflict == "error":
            raise PathConflictError(
                f"{join_path(storage_id, folder, name)} already exists"
            )
        if data.on_conflict == "keep_both" and await self._taken(
            storage_id, folder, name
        ):
            name, existing = await self._free_name(storage_id, folder, name), None
        path = join_path(storage_id, folder, name)
        source = TaskSource.URL if data.url else TaskSource.DEVICE
        params = {
            "path": path,
            "send_as": data.send_as,
            "on_conflict": data.on_conflict,
            "source": source,
        }
        if data.url:
            params["url"] = data.url
        task = TaskDB(
            status=TaskStatus.PENDING,
            stage=TaskStage.RECEIVING,
            bytes_total=data.size,
            callback_url=data.callback_url,
            batch_id=batch_id,
            path=path,
            params=params,
        )
        if existing and data.on_conflict == "skip":
            task.status, task.stage, task.file_id = (
                TaskStatus.DONE,
                TaskStage.DONE,
                existing.id,
            )
            task.warnings = ["skipped: file already exists"]
        try:
            await self._repo.create(task)
        except IntegrityError:
            await self._session.rollback()
            raise UploadInProgressError(f"{path} is already being uploaded") from None
        await self._session.commit()
        return task

    async def create(
        self, data: UploadServiceCreate, batch_id: UUID | None = None
    ) -> TaskServiceRead:
        task = await self.create_task_db(data, batch_id)
        if data.url and task.status == TaskStatus.PENDING:
            await self._claim(task)
            path = temp_path(task.path)

            async def receive() -> None:
                async with self._pipeline.slot(task, self._runtime.downloads):
                    reservation = self._runtime.disk.reserve(0)

                    def progress(done: int, total: int | None) -> None:
                        reservation.update(done, total)
                        self._pipeline.progress(task.id, done, total)

                    try:
                        await download(
                            data.url, path, await self._account.file_limit(), progress
                        )
                    finally:
                        reservation.release()

            self._runtime.spawn(self._pipeline.run(task, path, receive))
        return await self._tasks.get(task.id)

    async def create_batch(self, items: list[UploadServiceCreate]) -> BatchServiceRead:
        batch_id = uuid7()
        results = []
        for item in items:
            try:
                results.append(
                    BatchItemServiceRead(
                        path=item.path,
                        task=await self.create(item, batch_id),
                        error=None,
                    )
                )
            except TCloudError as error:
                await self._session.rollback()
                results.append(
                    BatchItemServiceRead(
                        path=item.path, task=None, error=error.to_dict()
                    )
                )
        return BatchServiceRead(batch_id=batch_id, items=results)

    async def _claim(self, task: TaskDB) -> None:
        claimed = await self._repo.claim(task.id)
        await self._session.commit()
        if not claimed:
            raise TaskNotPendingError("this task is not waiting for a file")
        self._pipeline.start(task)

    async def receive(
        self, task_id: UUID, body: AsyncIterator[bytes]
    ) -> TaskServiceRead:
        task = await self._repo.get_by_id(task_id)
        if not task:
            raise TaskNotFoundError("task not found")
        if task.params.get("source") != TaskSource.DEVICE:
            raise TaskNotPendingError("this task is not waiting for a file")
        reservation = self._runtime.disk.reserve(task.bytes_total)
        try:
            await self._claim(task)
        except BaseException:
            reservation.release()
            raise
        path = temp_path(task.path)
        try:

            def progress(done: int) -> None:
                reservation.update(done)
                self._pipeline.progress(task_id, done)

            limit = min(task.bytes_total, await self._account.file_limit())
            received = await write_body(body, path, limit, progress)
            if received != task.bytes_total:
                raise SizeMismatchError(
                    f"declared {task.bytes_total} bytes, received {received}"
                )
        except BaseException as error:
            path.unlink(missing_ok=True)
            await self._pipeline.fail(task, error)
            raise
        finally:
            reservation.release()
        self._runtime.spawn(self._pipeline.run(task, path))
        return await self._tasks.get(task_id)
