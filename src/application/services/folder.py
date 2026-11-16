from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import FolderNotFoundError, StorageRootError
from src.application.schemas import (
    FileServiceRead,
    FolderDeleteServiceRead,
    FolderListingServiceRead,
    FolderServiceRead,
)
from src.application.services.storage import StorageService
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.repositories import FileRepository, FolderRepository
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

    async def create(self, raw_path: str) -> FolderServiceRead:
        storage_id, path = split_folder(raw_path)
        if not path:
            raise StorageRootError("storage root always exists")
        await self._storage_service.get_db(storage_id)
        await self._repo.ensure(storage_id, path)
        await self._session.commit()
        return FolderServiceRead.from_db(await self._repo.get_by_path(storage_id, path))

    async def get_listing(self, raw_path: str) -> FolderListingServiceRead:
        storage_id, path = split_folder(raw_path)
        storage = await self._storage_service.get_db(storage_id)
        if not await self._repo.exists(storage_id, path):
            raise FolderNotFoundError("folder not found")
        folders = await self._repo.get_children(storage_id, path)
        files = await self._files.get_in_folder(storage_id, path)
        return FolderListingServiceRead(
            path=join_path(storage_id, path),
            folders=[FolderServiceRead.from_db(folder) for folder in folders],
            files=[FileServiceRead.from_db(file, storage.tg_chat_id) for file in files],
        )

    async def delete(self, raw_path: str) -> FolderDeleteServiceRead:
        storage_id, path = split_folder(raw_path)
        if not path:
            raise StorageRootError("storage root cannot be deleted")
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
