import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from typing import NamedTuple
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    EmptySearchError,
    FileChangedError,
    FolderNotFoundError,
    ForbiddenError,
    PreviewsDisabledError,
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
    PreviewsClearServiceRead,
)
from src.application.services.access import (
    allowed,
    leads_to_grant,
    read_scopes,
    require,
    require_root,
)
from src.application.services.storage import StorageService
from src.infrastructure.background import AppRuntime, enqueue
from src.infrastructure.cache import TaskStore
from src.infrastructure.database.models import FileDB, FilePartDB
from src.infrastructure.database.repositories import (
    FileRepository,
    FolderRepository,
    StorageRepository,
)
from src.infrastructure.enums import Permission, TaskPriority, UploadQueue
from src.infrastructure.integrations import (
    TelegramClient,
    TelegramFileReferenceError,
    TelegramMessageGoneError,
)
from src.infrastructure.security import cipher_size, cipher_span, decrypt, unseal
from src.utilities.errors import TCloudError
from src.utilities.parsers import join_path, split_file, split_folder

BACKFILL_TASK = "previews.backfill"
CLEAR_BATCH = 1000
SEARCH_FOLDERS = 20
MIME_FACETS = 200


def stored_size(file: FileDB) -> int:
    return cipher_size(file.size) if file.encryption_header else file.size


def file_tag(file: FileDB) -> str:
    return f'"{file.tg_message_id}"'


class Piece(NamedTuple):
    message_id: int
    offset: int
    size: int
    media: object


Reader = Callable[[Piece, int, int], AsyncIterator[bytes]]


def layout(file: FileDB, parts: list[FilePartDB]) -> list[tuple[int, int, int]]:
    return [(part.tg_message_id, part.offset, part.size) for part in parts] or [
        (file.tg_message_id, 0, stored_size(file))
    ]


async def open_pieces(
    load: Callable[[int], Awaitable[tuple[object, int]]],
    file: FileDB,
    parts: list[FilePartDB],
) -> list[Piece]:
    spans = layout(file, parts)
    found = await asyncio.gather(*(load(message_id) for message_id, _, _ in spans))
    for (message_id, _, size), (_, actual) in zip(spans, found):
        if actual != size:
            logger.warning(
                "File {} changed in Telegram: message {} has {} bytes, {} recorded",
                file.id,
                message_id,
                actual,
                size,
            )
            raise FileChangedError(
                "the file in Telegram was replaced, its size no longer matches"
            )
    return [Piece(*span, media) for span, (media, _) in zip(spans, found)]


def telegram_reader(telegram: TelegramClient) -> Reader:
    return lambda piece, offset, length: telegram.iter_media(
        piece.media, offset, length
    )


async def stored_body(
    read: Reader, pieces: list[Piece], offset: int, length: int
) -> AsyncIterator[bytes]:
    for piece in pieces:
        start = max(offset, piece.offset)
        stop = min(offset + length, piece.offset + piece.size)
        if start < stop:
            async for chunk in read(piece, start - piece.offset, stop - start):
                yield chunk


def file_body(
    read: Reader, pieces: list[Piece], file: FileDB, start: int, end: int
) -> AsyncIterator[bytes]:
    if not file.encryption_header:
        return stored_body(read, pieces, start, end - start + 1)
    offset, length = cipher_span(file.size, start, end)
    return decrypt(
        stored_body(read, pieces, offset, length),
        file.encryption_header,
        file.size,
        start,
        end,
    )


async def tracked(
    body: AsyncIterator[bytes], store: TaskStore, storage_id: UUID, kind: str
) -> AsyncIterator[bytes]:
    async with store.active(storage_id, kind):
        async for chunk in body:
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
        infos = await self._storage_service.channel_infos(list(set(chats.values())))
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
        parts = (await self._repo.get_parts([file.id])).get(file.id, [])
        messages = [message_id for message_id, _, _ in layout(file, parts)]
        async with self._runtime.store.active(file.storage_id, "write"):
            self._runtime.media.forget(chat_id, messages)
            await self._runtime.telegram.delete(chat_id, messages)
        await self._repo.delete_by_ids([file_id])
        await self._session.commit()
        logger.info("File {} deleted", file_id)

    async def open_download(
        self, actor: Actor, file_id: UUID, if_none_match: str | None = None
    ) -> tuple[FileDB, int, list[Piece] | None]:
        file, chat_id = await self.get_db(actor, file_id, Permission.READ)
        if if_none_match == file_tag(file):
            return file, chat_id, None
        return file, chat_id, await self._media(file, chat_id)

    async def backfill_previews(self, actor: Actor) -> None:
        require_root(actor)
        if not actor.settings.uploads.previews:
            raise PreviewsDisabledError("previews are turned off in root settings")
        await enqueue(UploadQueue.ARCHIVE, TaskPriority.LOW.level, BACKFILL_TASK)
        logger.info("Preview backfill queued by {}", actor.login)

    async def clear_previews(self, actor: Actor) -> PreviewsClearServiceRead:
        require_root(actor)
        cleared = freed = 0
        while True:
            count, size = await self._repo.clear_thumbnails(CLEAR_BATCH)
            await self._session.commit()
            if not count:
                break
            cleared += count
            freed += size
        logger.info(
            "Previews cleared by {}: {} files, {} bytes", actor.login, cleared, freed
        )
        return PreviewsClearServiceRead(cleared=cleared, bytes=freed)

    async def get_thumbnail(
        self, actor: Actor, file_id: UUID, if_none_match: str | None
    ) -> tuple[str, bytes | None]:
        file, chat_id = await self.get_db(actor, file_id, Permission.READ)
        tag = file_tag(file)
        if file.encryption_header:
            sealed = await self._repo.get_thumbnail(file.id)
            if not sealed:
                raise ThumbnailNotFoundError("this encrypted file has no thumbnail")
            if if_none_match == tag:
                return tag, None
            return tag, unseal(file.encryption_header, sealed)
        if if_none_match == tag:
            return tag, None
        data = await self._runtime.store.thumb(file.id, file.tg_message_id)
        if data is None:
            media = (await self._media(file, chat_id))[0].media
            try:
                data = await self._runtime.download.thumbnail(media)
            except TelegramFileReferenceError:
                logger.info("File {} thumbnail reference expired, re-reading", file.id)
                self._runtime.media.forget(chat_id, [file.tg_message_id])
                data = await self._runtime.download.thumbnail(
                    (await self._media(file, chat_id))[0].media
                )
            data = data or b""
            await self._runtime.store.keep_thumb(file.id, file.tg_message_id, data)
        if not data:
            raise ThumbnailNotFoundError("Telegram has no thumbnail for this file")
        return tag, data

    async def _media(self, file: FileDB, chat_id: int) -> list[Piece]:
        if not self._runtime.download.authorized:
            raise SessionNotAuthorizedError(
                "the download session is not logged in, log in with /api/telegram/download"
            )
        parts = (await self._repo.get_parts([file.id])).get(file.id, [])
        try:
            return await open_pieces(
                lambda message_id: self._lookup(chat_id, message_id), file, parts
            )
        except TelegramMessageGoneError:
            await self._repo.delete_by_ids([file.id])
            await self._session.commit()
            logger.info("File {} is gone from Telegram, record removed", file.id)
            await self._drop_parts(chat_id, [part.tg_message_id for part in parts])
            raise

    async def _lookup(self, chat_id: int, message_id: int) -> tuple[object, int]:
        return await self._runtime.media.load(
            self._runtime.download, chat_id, message_id
        )

    def body(
        self, file: FileDB, chat_id: int, pieces: list[Piece], start: int, end: int
    ) -> AsyncIterator[bytes]:
        return file_body(
            lambda piece, offset, length: self._retried(
                piece, file, chat_id, offset, length
            ),
            pieces,
            file,
            start,
            end,
        )

    async def _retried(
        self, piece: Piece, file: FileDB, chat_id: int, offset: int, length: int
    ) -> AsyncIterator[bytes]:
        sent = False
        try:
            async for chunk in self._runtime.download.iter_media(
                piece.media, offset, length
            ):
                sent = True
                yield chunk
            return
        except TelegramFileReferenceError:
            if sent:
                raise
        logger.info("File {} reference expired, re-reading", file.id)
        self._runtime.media.forget(chat_id, [piece.message_id])
        media, _ = await self._lookup(chat_id, piece.message_id)
        async for chunk in self._runtime.download.iter_media(media, offset, length):
            yield chunk

    async def _drop_parts(self, chat_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return
        self._runtime.media.forget(chat_id, message_ids)
        try:
            await self._runtime.telegram.delete(chat_id, message_ids)
        except TCloudError as error:
            logger.warning(
                "Parts {} left in Telegram chat {}: {}",
                message_ids,
                chat_id,
                error.message,
            )

    async def forget_messages(self, chat_id: int, message_ids: list[int]) -> None:
        storage = await self._storages.get_by_chat_id(chat_id)
        if not storage:
            return
        parts = await self._repo.part_messages(storage.id, message_ids)
        removed = await self._repo.delete_by_messages(storage.id, message_ids)
        await self._session.commit()
        await self._drop_parts(chat_id, sorted(set(parts) - set(message_ids)))
        if removed:
            logger.info(
                "{} files deleted in Telegram, records removed from storage {}",
                len(removed),
                storage.id,
            )
