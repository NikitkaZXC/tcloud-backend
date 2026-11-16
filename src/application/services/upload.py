from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid7

from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    FileTooLargeError,
    LengthRequiredError,
    PathConflictError,
    SizeMismatchError,
    TaskFinishedError,
    TaskNotFoundError,
    TaskNotQueuedError,
    TaskNotRetryableError,
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
from src.application.services.storage import StorageService
from src.application.services.task import TaskService
from src.application.services.temp import remove_task_files, temp_path, write_body
from src.application.services.webhook import send_hook
from src.config import settings
from src.infrastructure.background import AppRuntime, enqueue
from src.infrastructure.database.repositories import FileRepository
from src.infrastructure.database.session import session_factory
from src.infrastructure.enums import (
    TaskPriority,
    TaskSource,
    TaskStage,
    TaskStatus,
    UploadQueue,
)
from src.infrastructure.integrations.web import check_scheme
from src.infrastructure.media import check_name
from src.utilities.errors import TCloudError
from src.utilities.parsers import check_segment, join_path, split_file


class UploadService:
    def __init__(
        self,
        session: AsyncSession,
        runtime: AppRuntime,
        account_service: AccountService,
        storage_service: StorageService,
        task_service: TaskService,
    ) -> None:
        self._session = session
        self._runtime = runtime
        self._store = runtime.store
        self._account = account_service
        self._storages = storage_service
        self._tasks = task_service
        self._files = FileRepository(session)

    async def _taken(self, storage_id: UUID, folder: str, name: str) -> bool:
        if await self._files.get_by_path(storage_id, folder, name):
            return True
        return await self._store.path_locked(join_path(storage_id, folder, name))

    async def _free_name(self, storage_id: UUID, folder: str, name: str) -> str:
        stem, dot, suffix = name.rpartition(".")
        for index in range(1, 10_000):
            candidate = f"{stem} ({index}){dot}{suffix}"
            if not await self._taken(storage_id, folder, candidate):
                check_segment(candidate)
                return candidate
        raise PathConflictError("no free name left")

    async def _check_active(self) -> None:
        active = await self._store.count_active()
        if active >= settings.upload.MAX_ACTIVE_TASKS:
            raise TooManyTasksError(
                f"{active} uploads are already in progress, try later", retry_after=30
            )

    async def _new_task(
        self, data: UploadServiceCreate, batch_id: UUID | None
    ) -> tuple[dict, bool]:
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
        await self._check_active()
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
        priority = TaskPriority(data.priority)
        source = TaskSource.URL if data.url else TaskSource.DEVICE
        queue = UploadQueue.DOWNLOAD if data.url else UploadQueue.PREPARE
        task = {
            "id": uuid7(),
            "batch_id": batch_id,
            "status": TaskStatus.QUEUED,
            "stage": TaskStage.DOWNLOADING if data.url else TaskStage.SCANNING,
            "priority": priority,
            "priority_level": priority.level,
            "path": path,
            "bytes_done": 0,
            "bytes_total": data.size,
            "error": None,
            "warnings": [],
            "file_id": None,
            "waiting": {"reason": f"queue:{queue}"},
            "generation": 0,
            "params": {
                "source": source,
                "send_as": data.send_as,
                "on_conflict": data.on_conflict,
                "callback_url": data.callback_url,
                "url": data.url,
                "limit": limit,
                "file": str(temp_path(path)),
            },
        }
        if existing and data.on_conflict == "skip":
            task.update(
                status=TaskStatus.DONE,
                stage=TaskStage.DONE,
                file_id=existing.id,
                waiting=None,
                warnings=["skipped: file already exists"],
            )
            await self._store.create(task)
            return task, False
        if not await self._store.lock_path(path, task["id"]):
            raise UploadInProgressError(f"{path} is already being uploaded")
        return task, True

    async def _enqueue(self, task: dict, queue: UploadQueue, generation: int) -> None:
        await self._store.activate(task["id"])
        try:
            async with self._runtime.handoff_lock:
                await self._store.join_queue(task["id"], queue, task["priority_level"])
                await enqueue(
                    queue,
                    task["priority_level"],
                    task_id=str(task["id"]),
                    generation=generation,
                )
        except TCloudError as error:
            await self._store.update(
                task["id"], status=TaskStatus.FAILED, error=error.to_dict()
            )
            await self._store.release(task["id"], task["path"])
            raise

    async def upload(
        self, data: UploadServiceCreate, body: AsyncIterator[bytes]
    ) -> TaskServiceRead:
        if data.size is None:
            raise LengthRequiredError("Content-Length header is required")
        task, queued = await self._new_task(data, None)
        if not queued:
            return await self._tasks.get(task["id"])
        path = Path(task["params"]["file"])
        reservation = self._runtime.disk.reserve(data.size)
        try:
            limit = min(data.size, task["params"]["limit"])
            received = await write_body(body, path, limit, reservation.update)
            if received != data.size:
                raise SizeMismatchError(
                    f"declared {data.size} bytes, received {received}"
                )
            await self._store.create(task)
            await self._enqueue(task, UploadQueue.PREPARE, 0)
        except BaseException:
            path.unlink(missing_ok=True)
            await self._store.release(task["id"], task["path"])
            raise
        finally:
            reservation.release()
        return await self._tasks.get(task["id"])

    async def upload_url(
        self, data: UploadServiceCreate, batch_id: UUID | None = None
    ) -> TaskServiceRead:
        task, queued = await self._new_task(data, batch_id)
        if queued:
            await self._store.create(task)
            await self._enqueue(task, UploadQueue.DOWNLOAD, 0)
        return await self._tasks.get(task["id"])

    async def _get_raw(self, task_id: UUID) -> dict:
        task = await self._store.get(task_id)
        if not task:
            raise TaskNotFoundError("task not found")
        return task

    async def cancel(self, task_id: UUID) -> TaskServiceRead:
        result = await self._store.cancel(task_id)
        if result == "missing":
            raise TaskNotFoundError("task not found")
        if result == "finished":
            raise TaskFinishedError("task is already finished")
        if result == "cancelled":
            task = await self._get_raw(task_id)
            await self._store.release(task_id, task["path"])
            remove_task_files(task)
            if task["params"].get("callback_url"):
                self._runtime.spawn(
                    send_hook(
                        self._store,
                        session_factory,
                        task_id,
                        task["params"]["callback_url"],
                    )
                )
        return await self._tasks.get(task_id)

    async def set_priority(self, task_id: UUID, priority: str) -> TaskServiceRead:
        await self._get_raw(task_id)
        level = TaskPriority(priority).level
        requeued = await self._store.requeue(task_id, priority, level)
        if not requeued:
            raise TaskNotQueuedError("task is not waiting in a queue")
        generation, queue = requeued
        async with self._runtime.handoff_lock:
            await self._store.join_queue(task_id, queue, level)
            await enqueue(
                UploadQueue(queue), level, task_id=str(task_id), generation=generation
            )
        return await self._tasks.get(task_id)

    async def retry(self, task_id: UUID) -> TaskServiceRead:
        task = await self._get_raw(task_id)
        if task["status"] not in (TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise TaskNotRetryableError("only failed or cancelled tasks can be retried")
        if task["params"]["source"] != TaskSource.URL:
            raise TaskNotRetryableError(
                "files from a device are not kept: upload the file again"
            )
        await self._check_active()
        if not await self._store.lock_path(task["path"], task_id):
            raise UploadInProgressError(f"{task['path']} is already being uploaded")
        generation = (task.get("generation") or 0) + 1
        await self._store.update(
            task_id,
            status=TaskStatus.QUEUED,
            stage=TaskStage.DOWNLOADING,
            waiting={"reason": f"queue:{UploadQueue.DOWNLOAD}"},
            generation=generation,
            bytes_done=0,
            bytes_total=None,
            error=None,
            warnings=[],
            file_id=None,
            cancel=False,
            claimed=None,
            prepared=None,
            sent=None,
            committed=False,
        )
        await self._enqueue(task, UploadQueue.DOWNLOAD, generation)
        return await self._tasks.get(task_id)

    async def create_batch(self, items: list[UploadServiceCreate]) -> BatchServiceRead:
        batch_id = uuid7()
        results = []
        for item in items:
            try:
                task = await self.upload_url(item, batch_id)
                results.append(
                    BatchItemServiceRead(path=item.path, task=task, error=None)
                )
            except TCloudError as error:
                results.append(
                    BatchItemServiceRead(
                        path=item.path, task=None, error=error.to_dict()
                    )
                )
        return BatchServiceRead(batch_id=batch_id, items=results)
