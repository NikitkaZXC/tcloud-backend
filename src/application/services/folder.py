from uuid import UUID, uuid7

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    EmptySelectionError,
    FolderNotFoundError,
    ForbiddenError,
    SessionNotAuthorizedError,
    StorageMismatchError,
    StorageRootError,
    StoredFileNotFoundError,
    TooManyItemsError,
)
from src.application.schemas import (
    Actor,
    FileServiceRead,
    FolderDeleteServiceRead,
    FolderListingServiceRead,
    FolderServiceRead,
    FolderSizeServiceRead,
    TaskServiceRead,
)
from src.application.services.access import (
    allowed,
    leads_to_grant,
    read_scopes,
    require,
)
from src.application.services.storage import StorageService
from src.application.services.task import TaskService
from src.application.services.temp import temp_path
from src.application.services.upload import start_task
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.models import FileDB
from src.infrastructure.database.repositories import FileRepository, FolderRepository
from src.infrastructure.enums import (
    ArchiveFormat,
    Permission,
    TaskPriority,
    TaskSource,
    TaskStage,
    TaskStatus,
    TelegramSession,
    UploadQueue,
)
from src.infrastructure.integrations import session_exists
from src.utilities.parsers import join_path, split_folder


def common_folder(paths: list[str]) -> str:
    common: list[str] = []
    for segments in zip(*(path.split("/") if path else [] for path in paths)):
        if len(set(segments)) != 1:
            break
        common.append(segments[0])
    return "/".join(common)


class FolderService:
    def __init__(
        self,
        session: AsyncSession,
        runtime: AppRuntime,
        storage_service: StorageService,
        task_service: TaskService,
    ) -> None:
        self._session = session
        self._runtime = runtime
        self._storage_service = storage_service
        self._tasks = task_service
        self._repo = FolderRepository(session)
        self._files = FileRepository(session)

    async def create(self, actor: Actor, raw_path: str) -> FolderServiceRead:
        storage_id, path = split_folder(raw_path)
        if not path:
            raise StorageRootError("storage root always exists")
        require(actor, storage_id, path, Permission.UPLOAD)
        await self._storage_service.get_db(storage_id)
        await self._repo.ensure(storage_id, path)
        await self._session.commit()
        return FolderServiceRead.from_db(await self._repo.get_by_path(storage_id, path))

    async def get_listing(
        self, actor: Actor, raw_path: str
    ) -> FolderListingServiceRead:
        storage_id, path = split_folder(raw_path)
        readable = allowed(actor, storage_id, path, Permission.READ)
        if not readable and not leads_to_grant(actor, storage_id, path):
            raise ForbiddenError("no read access to this folder")
        storage = await self._storage_service.get_db(storage_id)
        if not await self._repo.exists(storage_id, path):
            raise FolderNotFoundError("folder not found")
        folders = [
            folder
            for folder in await self._repo.get_children(storage_id, path)
            if allowed(actor, storage_id, folder.path, Permission.READ)
            or leads_to_grant(actor, storage_id, folder.path)
        ]
        files = await self._files.get_in_folder(storage_id, path) if readable else []
        sizes = await self._files.sizes_below(
            storage_id, path, read_scopes(actor, storage_id)
        )
        return FolderListingServiceRead(
            path=join_path(storage_id, path),
            size=sum(size for _, size in sizes.values()),
            total_files=sum(count for count, _ in sizes.values()),
            folders=[
                FolderSizeServiceRead(
                    **FolderServiceRead.from_db(folder).model_dump(),
                    files=sizes.get(name, (0, 0))[0],
                    size=sizes.get(name, (0, 0))[1],
                )
                for folder in folders
                for name in [folder.path.rpartition("/")[2]]
            ],
            files=[FileServiceRead.from_db(file, storage.tg_chat_id) for file in files],
        )

    async def create_archive(
        self, actor: Actor, raw_path: str, archive_format: ArchiveFormat, priority: str
    ) -> TaskServiceRead:
        storage_id, path = split_folder(raw_path)
        self._require_listing(actor, storage_id, path)
        await self._storage_service.get_db(storage_id)
        if not await self._repo.exists(storage_id, path):
            raise FolderNotFoundError("folder not found")
        files = self._readable(actor, await self._files.get_below(storage_id, path))
        name = f"{path.rpartition('/')[2] or storage_id}.{archive_format}"
        return await self._queue_archive(
            actor, storage_id, path, files, archive_format, priority, name
        )

    async def create_selection_archive(
        self,
        actor: Actor,
        file_ids: list[UUID],
        folder_paths: list[str],
        archive_format: ArchiveFormat,
        priority: str,
        name: str | None,
    ) -> TaskServiceRead:
        if not file_ids and not folder_paths:
            raise EmptySelectionError("select at least one file or folder")
        limit = settings.upload.ARCHIVE_MAX_ITEMS
        if len(file_ids) + len(folder_paths) > limit:
            raise TooManyItemsError(
                f"an archive takes at most {limit} files and folders", limit=limit
            )
        folders = sorted({split_folder(raw) for raw in folder_paths})
        found = await self._files.get_many_with_chat(list(set(file_ids)))
        if len(found) < len(set(file_ids)):
            raise StoredFileNotFoundError("file not found")
        chosen = [file for file, _ in found.values()]
        storages = {storage_id for storage_id, _ in folders} | {
            file.storage_id for file in chosen
        }
        if len(storages) > 1:
            raise StorageMismatchError("all files and folders must be in one storage")
        storage_id = storages.pop()
        for file in chosen:
            require(actor, storage_id, file.folder_path, Permission.READ)
        for _, path in folders:
            self._require_listing(actor, storage_id, path)
        await self._storage_service.get_db(storage_id)
        files = {file.id: file for file in chosen}
        tops: list[str] = []
        for _, path in folders:
            if not await self._repo.exists(storage_id, path):
                raise FolderNotFoundError(
                    f"folder {join_path(storage_id, path)} not found"
                )
            if any(top == "" or path.startswith(f"{top}/") for top in tops):
                continue
            tops.append(path)
            for file in self._readable(
                actor, await self._files.get_below(storage_id, path)
            ):
                files.setdefault(file.id, file)
        base = common_folder(
            [path.rpartition("/")[0] for _, path in folders]
            + [file.folder_path for file in chosen]
        )
        title = name or f"{base.rpartition('/')[2] or storage_id}-{len(files)}-files"
        return await self._queue_archive(
            actor,
            storage_id,
            base,
            list(files.values()),
            archive_format,
            priority,
            f"{title}.{archive_format}",
        )

    @staticmethod
    def _require_listing(actor: Actor, storage_id: UUID, path: str) -> None:
        if not allowed(actor, storage_id, path, Permission.READ) and not leads_to_grant(
            actor, storage_id, path
        ):
            raise ForbiddenError("no read access to this folder")

    @staticmethod
    def _readable(actor: Actor, files: list[FileDB]) -> list[FileDB]:
        return [
            file
            for file in files
            if allowed(actor, file.storage_id, file.folder_path, Permission.READ)
        ]

    async def _queue_archive(
        self,
        actor: Actor,
        storage_id: UUID,
        base: str,
        files: list[FileDB],
        archive_format: ArchiveFormat,
        priority: str,
        name: str,
    ) -> TaskServiceRead:
        if not session_exists(TelegramSession.UPLOAD):
            raise SessionNotAuthorizedError(
                "the upload Telegram session is not logged in, use /api/telegram/upload"
            )
        sizes = [file.size for file in files]
        self._runtime.disk.check(sum(sizes) + max(sizes, default=0))
        level = TaskPriority(priority)
        task = {
            "id": uuid7(),
            "batch_id": None,
            "user_id": actor.user_id,
            "status": TaskStatus.QUEUED,
            "stage": TaskStage.ARCHIVING,
            "priority": level,
            "priority_level": level.level,
            "path": join_path(storage_id, base),
            "bytes_done": 0,
            "bytes_total": sum(sizes),
            "error": None,
            "warnings": [],
            "file_id": None,
            "waiting": {"reason": f"queue:{UploadQueue.ARCHIVE}"},
            "generation": 0,
            "archive": {"name": name, "format": archive_format, "files": len(files)},
            "params": {
                "source": TaskSource.ARCHIVE,
                "storage_id": storage_id,
                "folder": base,
                "format": archive_format,
                "file_ids": [file.id for file in files],
                "file": str(temp_path(f"archive.{archive_format}")),
            },
        }
        await self._runtime.store.create(task)
        await start_task(self._runtime, task, UploadQueue.ARCHIVE, 0)
        logger.info(
            "Archive of {} files from {} as {} queued",
            len(files),
            task["path"],
            archive_format,
        )
        return await self._tasks.get(task["id"], actor)

    async def delete(self, actor: Actor, raw_path: str) -> FolderDeleteServiceRead:
        storage_id, path = split_folder(raw_path)
        if not path:
            raise StorageRootError("storage root cannot be deleted")
        require(actor, storage_id, path, Permission.DELETE, subtree=True)
        storage = await self._storage_service.get_db(storage_id)
        if not await self._repo.exists(storage_id, path):
            raise FolderNotFoundError("folder not found")
        files = await self._files.get_messages_below(storage_id, path)
        await self._runtime.telegram.delete(
            storage.tg_chat_id, [message_id for _, message_id in files]
        )
        await self._files.delete_by_ids([file_id for file_id, _ in files])
        await self._repo.delete_with_children(storage_id, path)
        await self._session.commit()
        logger.info(
            "Folder {} deleted with {} files", join_path(storage_id, path), len(files)
        )
        return FolderDeleteServiceRead(
            path=join_path(storage_id, path), deleted_files=len(files)
        )
