from collections.abc import AsyncIterator
from datetime import UTC, datetime, time, timedelta
from time import monotonic
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    EmptySearchError,
    FileChangedError,
    FolderNotFoundError,
    ForbiddenError,
    SessionNotAuthorizedError,
    StoredFileNotFoundError,
    ThumbnailNotFoundError,
)
from src.application.schemas import (
    ActivityDayServiceRead,
    ActivityServiceRead,
    Actor,
    FileSearchServiceCreate,
    FileSearchServiceRead,
    FileServiceRead,
    FoundFileServiceRead,
    FoundFolderServiceRead,
    MimeCountServiceRead,
)
from src.application.services.access import (
    allowed,
    leads_to_grant,
    read_scopes,
    require,
)
from src.application.services.storage import StorageService
from src.infrastructure.background import AppRuntime
from src.infrastructure.cache import TaskStore
from src.infrastructure.database.models import FileDB
from src.infrastructure.database.repositories import (
    FileRepository,
    FolderRepository,
    StorageRepository,
)
from src.infrastructure.enums import Permission
from src.infrastructure.integrations import TelegramClient, TelegramMessageGoneError
from src.infrastructure.security import cipher_size, cipher_span, decrypt, unseal
from src.utilities.errors import TCloudError
from src.utilities.parsers import join_path, split_file, split_folder

SEARCH_FOLDERS = 20
MIME_FACETS = 200


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


async def tracked(
    body: AsyncIterator[bytes], store: TaskStore, storage_id: UUID, kind: str
) -> AsyncIterator[bytes]:
    marked = 0.0
    async for chunk in body:
        if monotonic() - marked >= 1:
            marked = monotonic()
            await store.mark_activity(storage_id, kind)
        yield chunk


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
        self._folders = FolderRepository(session)

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

    async def _scope(
        self, actor: Actor, raw_path: str | None
    ) -> tuple[UUID, str] | None:
        if raw_path is None:
            return None
        storage_id, folder = split_folder(raw_path)
        if not allowed(
            actor, storage_id, folder, Permission.READ
        ) and not leads_to_grant(actor, storage_id, folder):
            raise ForbiddenError("no read access to this folder")
        await self._storage_service.get_db(storage_id)
        if not await self._folders.exists(storage_id, folder):
            raise FolderNotFoundError("folder not found")
        return storage_id, folder

    async def _storage_names(self, chats: dict[UUID, int]) -> dict[UUID, str]:
        try:
            infos = await self._runtime.telegram.channels(list(set(chats.values())))
        except TCloudError as error:
            logger.warning(
                "Storage names for search are unavailable: {}", error.message
            )
            return {}
        return {
            storage_id: infos[chat_id].title
            for storage_id, chat_id in chats.items()
            if chat_id in infos
        }

    async def search(
        self, actor: Actor, data: FileSearchServiceCreate
    ) -> FileSearchServiceRead:
        query = (data.q or "").strip() or None
        file_filters = bool(
            data.kind
            or data.mime
            or data.size_min is not None
            or data.size_max is not None
            or data.date_from is not None
            or data.date_to is not None
            or data.encrypted is not None
        )
        if not query and not file_filters:
            raise EmptySearchError("give a query or at least one filter")
        scope = await self._scope(actor, data.path)
        scopes = read_scopes(actor)
        rows, total = await self._repo.search(
            scope=scope,
            scopes=scopes,
            query=query,
            kinds=data.kind,
            mimes=[mime.lower() for mime in data.mime],
            size_min=data.size_min,
            size_max=data.size_max,
            date_from=data.date_from,
            date_to=data.date_to,
            encrypted=data.encrypted,
            sort=data.sort,
            lang=data.lang,
            descending=(
                data.order or ("desc" if data.sort in ("size", "date") else "asc")
            )
            == "desc",
            limit=data.limit,
            offset=data.offset,
        )
        folders = (
            await self._folders.search(scope, scopes, query, SEARCH_FOLDERS)
            if query and not file_filters and data.offset == 0
            else []
        )
        chats = {file.storage_id: chat_id for file, chat_id in rows}
        missing = {folder.storage_id for folder in folders} - set(chats)
        for storage_id in missing:
            chats[storage_id] = (
                await self._storage_service.get_db(storage_id)
            ).tg_chat_id
        names = await self._storage_names(chats) if chats else {}
        return FileSearchServiceRead(
            items=[
                FoundFileServiceRead(
                    **FileServiceRead.from_db(file, chat_id).model_dump(),
                    folder=join_path(file.storage_id, file.folder_path),
                    storage_name=names.get(file.storage_id),
                )
                for file, chat_id in rows
            ],
            total=total,
            limit=data.limit,
            offset=data.offset,
            has_more=data.offset + len(rows) < total,
            folders=[
                FoundFolderServiceRead(
                    id=folder.id,
                    path=join_path(folder.storage_id, folder.path),
                    name=folder.path.rpartition("/")[2],
                    storage_id=folder.storage_id,
                    storage_name=names.get(folder.storage_id),
                    created_at=folder.created_at,
                    updated_at=folder.updated_at,
                )
                for folder in folders
            ],
        )

    async def activity(
        self, actor: Actor, raw_path: str | None, days: int
    ) -> ActivityServiceRead:
        scope = await self._scope(actor, raw_path)
        until = datetime.now(UTC).date()
        since = until - timedelta(days=days - 1)
        rows = await self._repo.activity(
            scope, read_scopes(actor), datetime.combine(since, time(), UTC)
        )
        return ActivityServiceRead(
            since=since,
            until=until,
            days=days,
            files=sum(count for _, count, _ in rows),
            size=sum(size for _, _, size in rows),
            items=[
                ActivityDayServiceRead(date=day, files=count, size=size)
                for day, count, size in rows
            ],
        )

    async def mime_counts(
        self, actor: Actor, raw_path: str | None
    ) -> list[MimeCountServiceRead]:
        scope = await self._scope(actor, raw_path)
        return [
            MimeCountServiceRead(mime=mime, files=count)
            for mime, count in await self._repo.mime_counts(
                scope, read_scopes(actor), MIME_FACETS
            )
        ]

    async def delete(self, actor: Actor, file_id: UUID) -> None:
        file, chat_id = await self.get_db(actor, file_id, Permission.DELETE)
        await self._runtime.store.mark_activity(file.storage_id, "write")
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
        tag = f'"{file.tg_message_id}"'
        if file.encryption_header:
            sealed = await self._repo.get_thumbnail(file.id)
            if not sealed:
                raise ThumbnailNotFoundError("this encrypted file has no thumbnail")
            if if_none_match == tag:
                return tag, None
            return tag, unseal(file.encryption_header, sealed)
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
