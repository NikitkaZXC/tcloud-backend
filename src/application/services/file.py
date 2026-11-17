from collections.abc import AsyncIterator
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    FileChangedError,
    SessionNotAuthorizedError,
    StoredFileNotFoundError,
    ThumbnailNotFoundError,
)
from src.application.schemas import Actor, FileServiceRead
from src.application.services.access import require
from src.application.services.storage import StorageService
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.models import FileDB
from src.infrastructure.database.repositories import FileRepository, StorageRepository
from src.infrastructure.enums import Permission
from src.infrastructure.integrations import TelegramClient, TelegramMessageGoneError
from src.infrastructure.security import cipher_size, cipher_span, decrypt
from src.utilities.parsers import split_file


def stored_size(file: FileDB) -> int:
    return cipher_size(file.size) if file.encryption_header else file.size


def file_body(
    telegram: TelegramClient, media: object, file: FileDB, start: int, end: int
) -> AsyncIterator[bytes]:
    if not file.encryption_header:
        return telegram.iter_media(media, start, end - start + 1)
    offset, length = cipher_span(file.size, start, end)
    return decrypt(
        telegram.iter_media(media, offset, length),
        file.encryption_header,
        file.size,
        start,
        end,
    )


class FileService:
    def __init__(
        self,
        session: AsyncSession,
        runtime: AppRuntime,
        storage_service: StorageService,
    ) -> None:
        self._session = session
        self._runtime = runtime
        self._storage_service = storage_service
        self._repo = FileRepository(session)
        self._storages = StorageRepository(session)

    async def get_db(
        self, actor: Actor, file_id: UUID, permission: Permission
    ) -> tuple[FileDB, int]:
        found = await self._repo.get_with_chat(file_id)
        if not found:
            raise StoredFileNotFoundError("file not found")
        require(actor, found[0].storage_id, found[0].folder_path, permission)
        return found

    async def get(self, actor: Actor, file_id: UUID) -> FileServiceRead:
        return FileServiceRead.from_db(
            *await self.get_db(actor, file_id, Permission.READ)
        )

    async def get_by_path(self, actor: Actor, raw_path: str) -> FileServiceRead:
        storage_id, folder, name = split_file(raw_path)
        require(actor, storage_id, folder, Permission.READ)
        storage = await self._storage_service.get_db(storage_id)
        file = await self._repo.get_by_path(storage_id, folder, name)
        if not file:
            raise StoredFileNotFoundError("file not found")
        return FileServiceRead.from_db(file, storage.tg_chat_id)

    async def delete(self, actor: Actor, file_id: UUID) -> None:
        file, chat_id = await self.get_db(actor, file_id, Permission.DELETE)
        await self._runtime.telegram.delete(chat_id, [file.tg_message_id])
        await self._repo.delete_by_ids([file_id])
        await self._session.commit()
        logger.info("File {} deleted", file_id)

    async def open_download(self, actor: Actor, file_id: UUID) -> tuple[FileDB, object]:
        file, chat_id = await self.get_db(actor, file_id, Permission.READ)
        return file, await self._media(file, chat_id)

    async def get_thumbnail(
        self, actor: Actor, file_id: UUID, if_none_match: str | None
    ) -> tuple[str, bytes | None]:
        file, chat_id = await self.get_db(actor, file_id, Permission.READ)
        if file.encryption_header:
            raise ThumbnailNotFoundError("encrypted files have no thumbnail")
        tag = f'"{file.tg_message_id}"'
        if if_none_match == tag:
            return tag, None
        data = await self._runtime.download.thumbnail(await self._media(file, chat_id))
        if not data:
            raise ThumbnailNotFoundError("Telegram has no thumbnail for this file")
        return tag, data

    async def _media(self, file: FileDB, chat_id: int) -> object:
        if not self._runtime.download.authorized:
            raise SessionNotAuthorizedError(
                "the download session is not logged in, log in with /api/telegram/download"
            )
        try:
            media, size = await self._runtime.download.message_media(
                chat_id, file.tg_message_id
            )
        except TelegramMessageGoneError:
            await self._repo.delete_by_ids([file.id])
            await self._session.commit()
            logger.info("File {} is gone from Telegram, record removed", file.id)
            raise
        if size != stored_size(file):
            logger.warning(
                "File {} changed in Telegram: {} bytes recorded, {} now",
                file.id,
                stored_size(file),
                size,
            )
            raise FileChangedError(
                "the file in Telegram was replaced, its size no longer matches"
            )
        return media

    async def forget_messages(self, chat_id: int, message_ids: list[int]) -> None:
        storage = await self._storages.get_by_chat_id(chat_id)
        if not storage:
            return
        removed = await self._repo.delete_by_messages(storage.id, message_ids)
        await self._session.commit()
        if removed:
            logger.info(
                "{} files deleted in Telegram, records removed from storage {}",
                len(removed),
                storage.id,
            )
