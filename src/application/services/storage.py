import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    ForbiddenError,
    StorageBusyError,
    StorageNotFoundError,
    StoragePhotoNotFoundError,
)
from src.application.schemas import (
    Actor,
    SettingsServiceRead,
    StorageActivityServiceRead,
    StorageServiceCreate,
    StorageServiceRead,
    StorageServiceUpdate,
)
from src.application.services.access import (
    can_see_storage,
    read_scopes,
    require_admin,
)
from src.application.services.temp import temp_path, write_body
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.models import StorageDB
from src.infrastructure.database.repositories import (
    StorageRepository,
    UserRepository,
)
from src.infrastructure.enums import MediaKind
from src.infrastructure.integrations import ChannelInfo
from src.infrastructure.limits import PHOTO_MAX
from src.infrastructure.media import UnsupportedMediaError, probe, to_jpeg
from src.utilities.errors import TCloudError

TELEGRAM_PHOTO_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png"}
CHAT_FOLDER = "TCloud"
NO_STATS = (0, 0, 0, 0)
CHANNEL_TTL = 600


async def root_settings(session: AsyncSession) -> SettingsServiceRead:
    root = await UserRepository(session).get_root()
    return SettingsServiceRead.from_stored(root.settings if root else {})[0]


class StorageService:
    def __init__(self, session: AsyncSession, runtime: AppRuntime) -> None:
        self._session = session
        self._runtime = runtime
        self._repo = StorageRepository(session)

    async def channel_infos(self, chat_ids: list[int]) -> dict[int, ChannelInfo]:
        cached = {
            chat_id: ChannelInfo(**info)
            for chat_id, info in (
                await self._runtime.store.channel_infos(chat_ids)
            ).items()
        }
        missing = [chat_id for chat_id in chat_ids if chat_id not in cached]
        if not missing:
            return cached
        try:
            fresh = await self._runtime.telegram.channels(missing)
        except TCloudError as error:
            logger.warning("Telegram channel info is unavailable: {}", error.message)
            return cached
        await self._runtime.store.keep_channel_infos(
            {chat_id: asdict(info) for chat_id, info in fresh.items()}, CHANNEL_TTL
        )
        return {**cached, **fresh}

    async def get_db(self, storage_id: UUID) -> StorageDB:
        storage = await self._repo.get_by_id(storage_id)
        if not storage:
            raise StorageNotFoundError("storage not found")
        return storage

    async def create(
        self, actor: Actor, data: StorageServiceCreate
    ) -> StorageServiceRead:
        require_admin(actor)
        chat_id = await self._runtime.telegram.create_channel(
            data.name, data.description or ""
        )
        self._runtime.channels = None
        storage = await self._repo.create(chat_id)
        await self._session.commit()
        logger.info("Storage {} created as channel {}", storage.id, chat_id)
        await self.sync_folder()
        return await self.get(actor, storage.id)

    async def delete(self, actor: Actor, storage_id: UUID, keep_channel: bool) -> None:
        require_admin(actor)
        storage = await self.get_db(storage_id)
        if await self._runtime.store.storage_busy(storage_id):
            raise StorageBusyError(
                "storage has uploads in progress, wait for them or cancel them"
            )
        if not keep_channel:
            await self._runtime.telegram.delete_channel(storage.tg_chat_id)
            self._runtime.channels = None
        await self._runtime.store.forget_channel(storage.tg_chat_id)
        await self._repo.delete(storage)
        await self._session.commit()
        logger.info(
            "Storage {} deleted, channel {} {}",
            storage_id,
            storage.tg_chat_id,
            "kept" if keep_channel else "deleted",
        )
        await self.sync_folder()

    async def activity(self, actor: Actor) -> list[StorageActivityServiceRead]:
        storage_ids = [
            storage.id
            for storage in await self._repo.get_all()
            if can_see_storage(actor, storage.id)
        ]
        flags = await self._runtime.store.activity(storage_ids)
        return [
            StorageActivityServiceRead(storage_id=storage_id, **flags[storage_id])
            for storage_id in storage_ids
        ]

    async def get_all(self, actor: Actor) -> list[StorageServiceRead]:
        storages = [
            storage
            for storage in await self._repo.get_all()
            if can_see_storage(actor, storage.id)
        ]
        infos = await self.channel_infos([storage.tg_chat_id for storage in storages])
        stats = await self._repo.get_stats(
            [storage.id for storage in storages], read_scopes(actor)
        )
        return [
            StorageServiceRead.from_db(
                storage, infos.get(storage.tg_chat_id), *stats.get(storage.id, NO_STATS)
            )
            for storage in storages
        ]

    def check_visible(self, actor: Actor, storage_id: UUID) -> None:
        if not can_see_storage(actor, storage_id):
            raise ForbiddenError("no access to this storage")

    async def get(self, actor: Actor, storage_id: UUID) -> StorageServiceRead:
        self.check_visible(actor, storage_id)
        storage = await self.get_db(storage_id)
        stats = await self._repo.get_stats([storage_id], read_scopes(actor, storage_id))
        info = (await self.channel_infos([storage.tg_chat_id])).get(storage.tg_chat_id)
        return StorageServiceRead.from_db(
            storage, info, *stats.get(storage_id, NO_STATS)
        )

    async def update(
        self, actor: Actor, storage_id: UUID, data: StorageServiceUpdate
    ) -> StorageServiceRead:
        require_admin(actor)
        storage = await self.get_db(storage_id)
        if data.name is not None:
            await self._runtime.telegram.edit_title(storage.tg_chat_id, data.name)
        if data.description is not None:
            await self._runtime.telegram.edit_about(
                storage.tg_chat_id, data.description
            )
        await self._runtime.store.forget_channel(storage.tg_chat_id)
        await self._repo.touch(storage_id)
        await self._session.commit()
        return await self.get(actor, storage_id)

    async def set_photo(
        self, actor: Actor, storage_id: UUID, body: AsyncIterator[bytes]
    ) -> StorageServiceRead:
        require_admin(actor)
        storage = await self.get_db(storage_id)
        path = temp_path()
        try:
            await write_body(body, path, PHOTO_MAX)
            media = await asyncio.to_thread(probe, path, None)
            if media.kind != MediaKind.PHOTO:
                raise UnsupportedMediaError("storage photo must be a still image")
            await self._runtime.scanner.scan(path)
            if suffix := TELEGRAM_PHOTO_SUFFIXES.get(media.mime):
                path = path.rename(path.with_suffix(suffix))
            else:
                jpeg = temp_path("photo.jpg")
                await asyncio.to_thread(to_jpeg, path, jpeg)
                path.unlink()
                path = jpeg
            await self._runtime.telegram.set_photo(storage.tg_chat_id, path)
            await self._runtime.store.forget_channel(storage.tg_chat_id)
        finally:
            path.unlink(missing_ok=True)
        await self._repo.touch(storage_id)
        await self._session.commit()
        return await self.get(actor, storage_id)

    async def delete_photo(self, actor: Actor, storage_id: UUID) -> StorageServiceRead:
        require_admin(actor)
        storage = await self.get_db(storage_id)
        await self._runtime.telegram.delete_photo(storage.tg_chat_id)
        await self._runtime.store.forget_channel(storage.tg_chat_id)
        await self._repo.touch(storage_id)
        await self._session.commit()
        return await self.get(actor, storage_id)

    async def get_photo(self, actor: Actor, storage_id: UUID) -> bytes:
        self.check_visible(actor, storage_id)
        storage = await self.get_db(storage_id)
        data = await self._runtime.telegram.photo(storage.tg_chat_id)
        if not data:
            raise StoragePhotoNotFoundError("storage has no photo")
        return data

    async def sync_folder(self) -> None:
        try:
            stored = await root_settings(self._session)
            chat_ids = (
                [storage.tg_chat_id for storage in await self._repo.get_all()]
                if stored.telegram.chat_folder
                else []
            )
            await self._runtime.telegram.sync_folder(CHAT_FOLDER, chat_ids)
        except Exception as error:
            logger.warning("Telegram folder {} is not synced: {}", CHAT_FOLDER, error)

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
        await self.sync_folder()
