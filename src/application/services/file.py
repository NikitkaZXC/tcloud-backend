from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import StoredFileNotFoundError
from src.application.schemas import FileServiceRead
from src.application.services.storage import StorageService
from src.infrastructure.background import BackgroundRuntime
from src.infrastructure.database.models import FileDB
from src.infrastructure.database.repositories import FileRepository
from src.utilities.parsers import split_file


class FileService:
    def __init__(
        self,
        session: AsyncSession,
        runtime: BackgroundRuntime,
        storage_service: StorageService,
    ) -> None:
        self._session = session
        self._runtime = runtime
        self._storage_service = storage_service
        self._repo = FileRepository(session)

    async def get_db(self, file_id: UUID) -> tuple[FileDB, int]:
        found = await self._repo.get_with_chat(file_id)
        if not found:
            raise StoredFileNotFoundError("file not found")
        return found

    async def get(self, file_id: UUID) -> FileServiceRead:
        return FileServiceRead.from_db(*await self.get_db(file_id))

    async def get_by_path(self, raw_path: str) -> FileServiceRead:
        storage_id, folder, name = split_file(raw_path)
        storage = await self._storage_service.get_db(storage_id)
        file = await self._repo.get_by_path(storage_id, folder, name)
        if not file:
            raise StoredFileNotFoundError("file not found")
        return FileServiceRead.from_db(file, storage.tg_chat_id)

    async def delete(self, file_id: UUID) -> None:
        file, chat_id = await self.get_db(file_id)
        await self._runtime.telegram.delete(chat_id, [file.tg_message_id])
        await self._repo.delete_by_ids([file_id])
        await self._session.commit()
        logger.info("File {} deleted", file_id)

    async def open_download(self, file_id: UUID) -> tuple[FileDB, object]:
        file, chat_id = await self.get_db(file_id)
        return file, await self._runtime.telegram.message_media(
            chat_id, file.tg_message_id
        )
