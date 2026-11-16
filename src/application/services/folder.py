from collections.abc import AsyncIterator

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    FolderNotFoundError,
    ForbiddenError,
    SessionNotAuthorizedError,
    StorageRootError,
)
from src.application.schemas import (
    Actor,
    FileServiceRead,
    FolderDeleteServiceRead,
    FolderListingServiceRead,
    FolderServiceRead,
)
from src.application.services.access import allowed, leads_to_grant, require
from src.application.services.archive import stream_archive
from src.application.services.storage import StorageService
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.repositories import FileRepository, FolderRepository
from src.infrastructure.enums import ArchiveFormat, Permission
from src.utilities.parsers import join_path, split_folder


class FolderService:
    def __init__(
        self,
        session: AsyncSession,
        runtime: AppRuntime,
        storage_service: StorageService,
    ) -> None:
        self._session = session
        self._runtime = runtime
        self._storage_service = storage_service
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
        return FolderListingServiceRead(
            path=join_path(storage_id, path),
            folders=[FolderServiceRead.from_db(folder) for folder in folders],
            files=[FileServiceRead.from_db(file, storage.tg_chat_id) for file in files],
        )

    async def open_archive(
        self, actor: Actor, raw_path: str, archive_format: ArchiveFormat
    ) -> tuple[str, AsyncIterator[bytes]]:
        storage_id, path = split_folder(raw_path)
        if not allowed(actor, storage_id, path, Permission.READ) and not leads_to_grant(
            actor, storage_id, path
        ):
            raise ForbiddenError("no read access to this folder")
        storage = await self._storage_service.get_db(storage_id)
        if not await self._repo.exists(storage_id, path):
            raise FolderNotFoundError("folder not found")
        if not self._runtime.download.authorized:
            raise SessionNotAuthorizedError(
                "the download session is not logged in, log in with /api/telegram/download"
            )
        files = [
            file
            for file in await self._files.get_below(storage_id, path)
            if allowed(actor, storage_id, file.folder_path, Permission.READ)
        ]
        sizes = [file.size for file in files]
        size = sum(sizes) + max(sizes, default=0)
        self._runtime.disk.check(size)
        logger.info(
            "Archiving {} files from {} as {}",
            len(files),
            join_path(storage_id, path),
            archive_format,
        )
        name = f"{path.rpartition('/')[2] or storage_id}.{archive_format}"
        return name, stream_archive(
            self._runtime.download,
            storage.tg_chat_id,
            files,
            path,
            archive_format,
            self._runtime.disk,
            size,
        )

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
