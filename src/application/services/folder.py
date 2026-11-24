import base64
import binascii
import json
from datetime import datetime
from uuid import UUID, uuid7

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    ArchiveInProgressError,
    EmptySelectionError,
    FileTooLargeError,
    FolderNotFoundError,
    ForbiddenError,
    InvalidCursorError,
    SessionNotAuthorizedError,
    StorageMismatchError,
    StorageRootError,
    StoredFileNotFoundError,
    TooManyItemsError,
)
from src.application.schemas import (
    Actor,
    FileEntryServiceRead,
    FileServiceRead,
    FolderDeleteServiceRead,
    FolderEntryServiceRead,
    FolderListingServiceCreate,
    FolderListingServiceRead,
    FolderServiceRead,
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
from src.application.services.upload import hold_quota, start_task, task_priority
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.models import FileDB, FolderDB
from src.infrastructure.database.repositories import FileRepository, FolderRepository
from src.infrastructure.enums import (
    ArchiveFormat,
    Permission,
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


def encode_cursor(sort: list, group: int, keys: list, ident: UUID) -> str:
    raw = json.dumps(
        {
            "s": sort,
            "g": group,
            "k": [
                key.isoformat() if isinstance(key, datetime) else key for key in keys
            ],
            "i": str(ident),
        },
        ensure_ascii=False,
    )
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, sort: list) -> tuple[int, list, UUID]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if raw["s"] != sort or raw["g"] not in (0, 1):
            raise ValueError("cursor of another sort")
        keys = raw["k"]
        if sort[0] in ("modified", "uploaded"):
            keys = [datetime.fromisoformat(keys[0])]
        elif sort[0] == "size":
            keys = [int(keys[0])]
        elif not all(isinstance(key, str) for key in keys):
            raise ValueError("bad cursor keys")
        if len(keys) != (2 if sort[0] == "type" else 1):
            raise ValueError("bad cursor keys")
        return raw["g"], keys, UUID(raw["i"])
    except (ValueError, KeyError, TypeError, binascii.Error) as error:
        raise InvalidCursorError(
            "the cursor is invalid or belongs to another sort, start from the first page"
        ) from error


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
        self, actor: Actor, raw_path: str, params: FolderListingServiceCreate
    ) -> FolderListingServiceRead:
        ui = actor.settings.interface
        sort_by = params.sort_by or ui.sort_by
        sort_order = params.sort_order or ui.sort_order
        folders_first = (
            ui.folders_first if params.folders_first is None else params.folders_first
        )
        lang = params.lang or ui.language or "en"
        sort = [sort_by, sort_order, folders_first, lang]
        after = decode_cursor(params.cursor, sort) if params.cursor else None
        storage_id, path = split_folder(raw_path)
        readable = allowed(actor, storage_id, path, Permission.READ)
        if not readable and not leads_to_grant(actor, storage_id, path):
            raise ForbiddenError("no read access to this folder")
        await self._storage_service.get_db(storage_id)
        if not await self._repo.exists(storage_id, path):
            raise FolderNotFoundError("folder not found")
        first = after is None
        with_folders = not (folders_first and after and after[0] == 1)
        folders = {
            folder.id: folder
            for folder in (
                await self._repo.get_children(storage_id, path) if with_folders else []
            )
            if allowed(actor, storage_id, folder.path, Permission.READ)
            or leads_to_grant(actor, storage_id, folder.path)
        }
        scopes = read_scopes(actor, storage_id)
        sizes = (
            await self._files.sizes_below(storage_id, path, scopes)
            if first or (folders and sort_by == "size")
            else None
        )

        def size_of(folder: FolderDB) -> int:
            return sizes.get(folder.path.rpartition("/")[2], (0, 0))[1] if sizes else 0

        rows = await self._files.list_page(
            storage_id=storage_id,
            folder=path,
            files=readable,
            folders=[
                (
                    folder.id,
                    folder.path.rpartition("/")[2],
                    size_of(folder),
                    folder.updated_at,
                    folder.created_at,
                )
                for folder in folders.values()
            ],
            sort_by=sort_by,
            lang=lang,
            descending=sort_order == "desc",
            folders_first=folders_first,
            after=after,
            limit=params.limit + 1,
        )
        page = rows[: params.limit]
        on_page = [folders[ident] for group, ident, _ in page if group == 0]
        if sizes is None and on_page:
            sizes = await self._files.sizes_below(
                storage_id,
                path,
                scopes,
                [folder.path.rpartition("/")[2] for folder in on_page],
            )
        entries = {
            folder.id: FolderEntryServiceRead(
                **FolderServiceRead.from_db(folder).model_dump(),
                files=sizes.get(name, (0, 0))[0],
                size=sizes.get(name, (0, 0))[1],
            )
            for folder in on_page
            for name in [folder.path.rpartition("/")[2]]
        }
        file_ids = [ident for group, ident, _ in page if group == 1]
        files = await self._files.get_many_with_chat(file_ids) if file_ids else {}
        totals = sizes.values() if first else None
        return FolderListingServiceRead(
            path=join_path(storage_id, path),
            size=sum(size for _, size in totals) if first else None,
            total_files=sum(count for count, _ in totals) if first else None,
            total_folders=len(folders) if first else None,
            direct_files=sizes.get(None, (0, 0))[0] if first else None,
            items=[
                (
                    entries[ident]
                    if group == 0
                    else FileEntryServiceRead(
                        **FileServiceRead.from_db(*files[ident]).model_dump()
                    )
                )
                for group, ident, _ in page
                if group == 0 or ident in files
            ],
            next_cursor=(
                encode_cursor(sort, page[-1][0], page[-1][2], page[-1][1])
                if len(rows) > params.limit
                else None
            ),
        )

    async def create_archive(
        self,
        actor: Actor,
        raw_path: str,
        archive_format: ArchiveFormat | None,
        priority: str | None,
    ) -> TaskServiceRead:
        archive_format = archive_format or actor.settings.archives.format
        priority = priority or actor.settings.archives.priority
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
        archive_format: ArchiveFormat | None,
        priority: str | None,
        name: str | None,
    ) -> TaskServiceRead:
        archive_format = archive_format or actor.settings.archives.format
        priority = priority or actor.settings.archives.priority
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

    @staticmethod
    def _check_archive_limits(count: int, size: int) -> None:
        files = settings.upload.ARCHIVE_MAX_FILES
        if files and count > files:
            raise TooManyItemsError(
                f"an archive takes at most {files} files, this one has {count}",
                limit=files,
            )
        limit = settings.upload.ARCHIVE_MAX_SIZE_MB * 1024 * 1024
        if limit and size > limit:
            raise FileTooLargeError(
                f"files of an archive take at most {limit} bytes, these take {size}",
                limit=limit,
            )

    async def _archive_running(self, actor: Actor) -> bool:
        store = self._runtime.store
        tasks = await store.get_many(
            await store.active_ids(), ("user_id", "status", "params")
        )
        return any(
            task.get("user_id") == str(actor.user_id)
            and task["params"]["source"] == TaskSource.ARCHIVE
            and task["status"] not in TaskStatus.finished()
            for task in tasks
            if task.get("params")
        )

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
        self._check_archive_limits(len(files), sum(sizes))
        task_id = uuid7()
        level = task_priority(actor, priority)
        task = {
            "id": task_id,
            "batch_id": None,
            "user_id": actor.user_id,
            "status": TaskStatus.QUEUED,
            "stage": TaskStage.ARCHIVING,
            "step": UploadQueue.ARCHIVE.step,
            "priority": level,
            "priority_level": level.level,
            "source": TaskSource.ARCHIVE,
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
        store = self._runtime.store
        async with store.disk_lock():
            if await self._archive_running(actor):
                raise ArchiveInProgressError(
                    "your previous archive is still being built, wait or cancel it"
                )
            needed = sum(sizes) + max(sizes, default=0)
            await hold_quota(store, actor, task_id, needed)
            await self._runtime.disk.hold(task_id, needed)
            await store.create(task)
            await store.activate(task_id)
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
        async with self._runtime.store.active(storage_id, "write"):
            messages = [message_id for _, message_id in files]
            self._runtime.media.forget(storage.tg_chat_id, messages)
            await self._runtime.telegram.delete(storage.tg_chat_id, messages)
        file_ids = list({file_id for file_id, _ in files})
        await self._files.delete_by_ids(file_ids)
        await self._repo.delete_with_children(storage_id, path)
        await self._session.commit()
        logger.info(
            "Folder {} deleted with {} files",
            join_path(storage_id, path),
            len(file_ids),
        )
        return FolderDeleteServiceRead(
            path=join_path(storage_id, path), deleted_files=len(file_ids)
        )
