import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import StorageNotFoundError, StoragePhotoNotFoundError
from src.application.schemas import (
    StorageServiceCreate,
    StorageServiceRead,
    StorageServiceUpdate,
)
from src.application.services.temp import temp_path, write_body
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.models import StorageDB
from src.infrastructure.database.repositories import StorageRepository
from src.infrastructure.enums import MediaKind
from src.infrastructure.limits import PHOTO_MAX
from src.infrastructure.media import UnsupportedMediaError, probe, to_jpeg

TELEGRAM_PHOTO_MIMES = ("image/jpeg", "image/png")


class StorageService:
    def __init__(self, session: AsyncSession, runtime: AppRuntime) -> None:
        self._session = session
        self._runtime = runtime
        self._repo = StorageRepository(session)

    async def get_db(self, storage_id: UUID) -> StorageDB:
        storage = await self._repo.get_by_id(storage_id)
        if not storage:
            raise StorageNotFoundError("storage not found")
        return storage

    async def create(self, data: StorageServiceCreate) -> StorageServiceRead:
        chat_id = await self._runtime.telegram.create_channel(
            data.name, data.description or ""
        )
        storage = await self._repo.create(chat_id)
        await self._session.commit()
        logger.info("Storage {} created as channel {}", storage.id, chat_id)
        return await self.get(storage.id)

    async def get_all(self) -> list[StorageServiceRead]:
        storages = await self._repo.get_all()
        infos = await self._runtime.telegram.channels(
            [storage.tg_chat_id for storage in storages]
        )
        return [
            StorageServiceRead.from_db(storage, infos.get(storage.tg_chat_id))
            for storage in storages
        ]

    async def get(self, storage_id: UUID) -> StorageServiceRead:
        storage = await self.get_db(storage_id)
        files, total_size = await self._repo.get_stats(storage_id)
        info = (await self._runtime.telegram.channels([storage.tg_chat_id])).get(
            storage.tg_chat_id
        )
        return StorageServiceRead.from_db(
            storage, info, files=files, total_size=total_size
        )

    async def update(
        self, storage_id: UUID, data: StorageServiceUpdate
    ) -> StorageServiceRead:
        storage = await self.get_db(storage_id)
        if data.name is not None:
            await self._runtime.telegram.edit_title(storage.tg_chat_id, data.name)
        if data.description is not None:
            await self._runtime.telegram.edit_about(
                storage.tg_chat_id, data.description
            )
        await self._repo.touch(storage_id)
        await self._session.commit()
        return await self.get(storage_id)

    async def set_photo(
        self, storage_id: UUID, body: AsyncIterator[bytes]
    ) -> StorageServiceRead:
        storage = await self.get_db(storage_id)
        path = temp_path()
        try:
            await write_body(body, path, PHOTO_MAX)
            media = await asyncio.to_thread(probe, path, None)
            if media.kind != MediaKind.PHOTO:
                raise UnsupportedMediaError("storage photo must be a still image")
            await self._runtime.scanner.scan(path)
            if media.mime not in TELEGRAM_PHOTO_MIMES:
                jpeg = temp_path("photo.jpg")
                await asyncio.to_thread(to_jpeg, path, jpeg)
                path.unlink()
                path = jpeg
            await self._runtime.telegram.set_photo(storage.tg_chat_id, path)
        finally:
            path.unlink(missing_ok=True)
        await self._repo.touch(storage_id)
        await self._session.commit()
        return await self.get(storage_id)

    async def delete_photo(self, storage_id: UUID) -> StorageServiceRead:
        storage = await self.get_db(storage_id)
        await self._runtime.telegram.delete_photo(storage.tg_chat_id)
        await self._repo.touch(storage_id)
        await self._session.commit()
        return await self.get(storage_id)

    async def get_photo(self, storage_id: UUID) -> bytes:
        storage = await self.get_db(storage_id)
        data = await self._runtime.telegram.photo(storage.tg_chat_id)
        if not data:
            raise StoragePhotoNotFoundError("storage has no photo")
        return data

    async def connect_chats(self, chat_ids: list[int]) -> None:
        for chat_id in chat_ids:
            if await self._repo.exists_chat(chat_id):
                continue
            try:
                await self._runtime.telegram.check_channel(chat_id)
            except Exception as error:
                logger.error(
                    "Storage {} from TELEGRAM_CHATS is not connected: {}",
                    chat_id,
                    error,
                )
                continue
            await self._repo.create(chat_id)
            await self._session.commit()
            logger.info("Storage {} connected from TELEGRAM_CHATS", chat_id)
