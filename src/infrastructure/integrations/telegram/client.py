import asyncio
import inspect
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, contextmanager
from functools import wraps
from pathlib import Path

from loguru import logger
from telethon import TelegramClient as TelethonClient
from telethon import errors, events, functions, helpers, types, utils

from src.infrastructure.enums import MediaKind
from src.infrastructure.integrations.telegram.exception import (
    ChannelCheckError,
    TelegramChannelsLimitError,
    TelegramCodeInvalidError,
    TelegramError,
    TelegramFloodWaitError,
    TelegramMessageGoneError,
    TelegramPasswordInvalidError,
    TelegramPasswordNeededError,
    TelegramPhoneInvalidError,
    TelegramSessionTooFreshError,
    TelegramUnauthorizedError,
    TelegramUnavailableError,
)
from src.infrastructure.integrations.telegram.schemas import (
    Authorization,
    ChannelInfo,
    SentFile,
)
from src.infrastructure.limits import (
    BATCH,
    BIG_FILE,
    CHANNELS_MAX,
    CHANNELS_MAX_PREMIUM,
    CHUNK,
    FLOOD_SLEEP_THRESHOLD,
    FOLDER_IDS,
    FOLDER_PEERS_MAX,
    FOLDER_PEERS_MAX_PREMIUM,
    THUMB_SIDE,
)
from src.infrastructure.media import Media
from src.utilities.errors import TCloudError

RECONNECT_MIN = 1.0
RECONNECT_MAX = 60.0
CONNECT_TIMEOUT = 15.0
PING_TTL = 5.0
PING_TIMEOUT = 5.0

CHANNEL_ACTIONS = (
    types.MessageActionChatEditTitle,
    types.MessageActionChatEditPhoto,
    types.MessageActionChatDeletePhoto,
)

TRANSIENT = (
    ConnectionError,
    OSError,
    TimeoutError,
    errors.ServerError,
    errors.TimedOutError,
    errors.RpcCallFailError,
    errors.FilePartMissingError,
)


@contextmanager
def translated():
    try:
        yield
    except errors.FloodWaitError as error:
        raise TelegramFloodWaitError(error.seconds) from error
    except TRANSIENT as error:
        raise TelegramUnavailableError(str(error) or type(error).__name__) from error
    except errors.SessionPasswordNeededError as error:
        raise TelegramPasswordNeededError(
            "the account has two-step verification, send the password"
        ) from error
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError) as error:
        raise TelegramCodeInvalidError("the login code is wrong or expired") from error
    except errors.PasswordHashInvalidError as error:
        raise TelegramPasswordInvalidError(
            "the two-step verification password is wrong"
        ) from error
    except errors.PhoneNumberInvalidError as error:
        raise TelegramPhoneInvalidError(
            "Telegram does not accept this phone number"
        ) from error
    except errors.FreshResetAuthorisationForbiddenError as error:
        raise TelegramSessionTooFreshError(
            "Telegram lets a session end other sessions only 24 h after login"
        ) from error
    except errors.UnauthorizedError as error:
        raise TelegramUnauthorizedError(
            f"Telegram session is not authorized: {error}"
        ) from error
    except errors.RPCError as error:
        raise TelegramError(str(error)) from error


def translate_errors(method):
    if inspect.isasyncgenfunction(method):

        @wraps(method)
        async def generator(*args, **kwargs):
            with translated():
                if args and isinstance(args[0], TelegramClient):
                    await args[0].ensure_connected()
                async with aclosing(method(*args, **kwargs)) as items:
                    async for item in items:
                        yield item

        return generator

    @wraps(method)
    async def wrapper(*args, **kwargs):
        with translated():
            if args and isinstance(args[0], TelegramClient):
                await args[0].ensure_connected()
            return await method(*args, **kwargs)

    return wrapper


def photo_bytes(size: types.TypePhotoSize) -> int:
    return size.size if isinstance(size, types.PhotoSize) else max(size.sizes)


def photo_size(sizes: list[types.TypePhotoSize]) -> types.TypePhotoSize | None:
    return max(
        (
            size
            for size in sizes
            if isinstance(size, (types.PhotoSize, types.PhotoSizeProgressive))
        ),
        key=photo_bytes,
        default=None,
    )


def media_size(media) -> int | None:
    photo = getattr(media, "photo", None)
    if isinstance(photo, types.Photo) and (best := photo_size(photo.sizes)):
        return photo_bytes(best)
    document = getattr(media, "document", None)
    return document.size if isinstance(document, types.Document) else None


def download_location(media) -> tuple[object, dict]:
    photo = getattr(media, "photo", None)
    best = photo_size(photo.sizes) if isinstance(photo, types.Photo) else None
    if not best:
        return media, {}
    location = types.InputPhotoFileLocation(
        id=photo.id,
        access_hash=photo.access_hash,
        file_reference=photo.file_reference,
        thumb_size=best.type,
    )
    return location, {"dc_id": photo.dc_id, "file_size": photo_bytes(best)}


class TelegramClient:
    _wanted = False
    _retry_at = 0.0
    _backoff = 0.0
    _reconnect_lock: asyncio.Lock | None = None
    _pinged: tuple[float, bool] | None = None
    _me_id: int | None = None

    def __init__(
        self,
        session: Path,
        api_id: int,
        api_hash: str,
        upload_workers: int = 4,
        receive_updates: bool = False,
        device_model: str = "tcloud",
    ) -> None:
        self.upload_workers = upload_workers
        self.authorized = False
        session.parent.mkdir(parents=True, exist_ok=True)
        self._client = TelethonClient(
            str(session),
            api_id,
            api_hash,
            flood_sleep_threshold=FLOOD_SLEEP_THRESHOLD,
            device_model=device_model,
            receive_updates=receive_updates,
        )

    async def ensure_connected(self) -> None:
        if not self._wanted or self._client.is_connected():
            return
        self._reconnect_lock = self._reconnect_lock or asyncio.Lock()
        async with self._reconnect_lock:
            if self._client.is_connected():
                return
            wait = self._retry_at - time.monotonic()
            if wait > 0:
                raise TelegramUnavailableError(
                    f"Telegram is disconnected, next reconnect in {wait:.0f} s"
                )
            try:
                await asyncio.wait_for(self._client.connect(), CONNECT_TIMEOUT)
            except (*TRANSIENT, asyncio.TimeoutError) as error:
                self._backoff = min(
                    max(self._backoff * 2, RECONNECT_MIN), RECONNECT_MAX
                )
                self._retry_at = time.monotonic() + self._backoff
                logger.warning(
                    "Telegram {}: reconnect failed ({!r}), next try in {:.0f} s",
                    self._name,
                    error,
                    self._backoff,
                )
                raise TelegramUnavailableError(
                    f"Telegram is disconnected: {error!r}"
                ) from error
            self._backoff = self._retry_at = 0.0
            logger.info("Telegram {}: reconnected", self._name)

    @translate_errors
    async def start(self) -> bool:
        self._wanted = False
        await self._client.connect()
        self._wanted = True
        self.authorized = await self._client.is_user_authorized()
        if not self.authorized:
            logger.warning("Telegram session {} is not authorized", self._name)
            return False
        me = await self._client.get_me()
        self._me_id = me.id
        if me.bot:
            raise RuntimeError(
                "tcloud needs a user account: bots cannot create channels"
            )
        logger.info(
            "Telegram {}: logged in as {} (premium={})",
            self._name,
            me.username or me.id,
            bool(me.premium),
        )
        return True

    @property
    def _name(self) -> str:
        return self._client.session.filename

    async def stop(self) -> None:
        self._wanted = False
        await self._client.disconnect()

    def connected(self) -> bool:
        return self._client.is_connected()

    @translate_errors
    async def _get_state(self) -> None:
        await self._client(functions.updates.GetStateRequest())

    async def ping(self) -> bool:
        if not self.authorized:
            return False
        if self._pinged and time.monotonic() - self._pinged[0] < PING_TTL:
            return self._pinged[1]
        try:
            await asyncio.wait_for(self._get_state(), PING_TIMEOUT)
            alive = True
        except (TCloudError, TimeoutError) as error:
            logger.warning("Telegram {}: ping failed: {!r}", self._name, error)
            alive = False
        self._pinged = (time.monotonic(), alive)
        return alive

    async def log_out(self) -> None:
        if not (self.authorized and await self._client.log_out()):
            await self.forget()
        self._wanted = self.authorized = False

    async def forget(self) -> None:
        self._wanted = False
        await self._client.disconnect()
        Path(self._name).unlink(missing_ok=True)
        self.authorized = False

    @translate_errors
    async def authorizations(self) -> list[Authorization]:
        result = await self._client(functions.account.GetAuthorizationsRequest())
        return [
            Authorization(
                item.hash,
                item.device_model,
                item.date_created,
                item.date_active,
                item.ip,
                item.country,
                bool(item.current),
                item.api_id,
            )
            for item in result.authorizations
        ]

    @translate_errors
    async def revoke(self, authorization_hash: int) -> None:
        await self._client(
            functions.account.ResetAuthorizationRequest(hash=authorization_hash)
        )

    async def watch(
        self,
        deleted: Callable[[int, list[int]], Awaitable[None]],
        channel_changed: Callable[[int], Awaitable[None]],
        account_changed: Callable[[], Awaitable[None]],
    ) -> None:
        async def on_deleted(event: events.MessageDeleted.Event) -> None:
            if event.chat_id is not None:
                await deleted(event.chat_id, list(event.deleted_ids))

        async def on_update(update) -> None:
            if isinstance(update, types.UpdateChannel):
                await channel_changed(
                    utils.get_peer_id(types.PeerChannel(update.channel_id))
                )
            elif isinstance(update, types.UpdateNewChannelMessage):
                if isinstance(getattr(update.message, "action", None), CHANNEL_ACTIONS):
                    await channel_changed(utils.get_peer_id(update.message.peer_id))
            elif update.user_id == self._me_id:
                await account_changed()

        self._client.add_event_handler(on_deleted, events.MessageDeleted())
        self._client.add_event_handler(
            on_update,
            events.Raw(
                [types.UpdateChannel, types.UpdateNewChannelMessage, types.UpdateUser]
            ),
        )
        await self._client.catch_up()

    @translate_errors
    async def is_premium(self) -> bool:
        return bool((await self._client.get_me()).premium)

    @translate_errors
    async def channels_used(self) -> int:
        return sum(
            [1 async for dialog in self._client.iter_dialogs() if dialog.is_channel]
        )

    async def _entity(self, chat_id: int):
        try:
            return await self._client.get_entity(chat_id)
        except ValueError:
            await self._client.get_dialogs()
            return await self._client.get_entity(chat_id)

    @translate_errors
    async def create_channel(self, title: str, about: str) -> int:
        try:
            result = await self._client(
                functions.channels.CreateChannelRequest(
                    title=title, about=about, broadcast=True
                )
            )
        except errors.ChannelsTooMuchError as error:
            raise TelegramChannelsLimitError(
                f"the account is in too many channels and groups ({CHANNELS_MAX}, "
                f"{CHANNELS_MAX_PREMIUM} with Premium): delete a storage or leave "
                "other channels in Telegram",
                limit=CHANNELS_MAX_PREMIUM if await self.is_premium() else CHANNELS_MAX,
            ) from error
        return utils.get_peer_id(result.chats[0])

    @translate_errors
    async def sync_folder(self, title: str, chat_ids: list[int]) -> None:
        result = await self._client(functions.messages.GetDialogFiltersRequest())
        folders = [
            item for item in result.filters if isinstance(item, types.DialogFilter)
        ]
        folder = next((item for item in folders if item.title.text == title), None)
        limit = (
            FOLDER_PEERS_MAX_PREMIUM if await self.is_premium() else FOLDER_PEERS_MAX
        )
        peers = []
        for chat_id in chat_ids:
            if len(peers) == limit:
                logger.warning(
                    "Telegram folder {} holds only {} of {} channels",
                    title,
                    limit,
                    len(chat_ids),
                )
                break
            try:
                peers.append(utils.get_input_peer(await self._entity(chat_id)))
            except ValueError as error:
                logger.warning("Telegram channel {} is unavailable: {}", chat_id, error)
        if not peers:
            if folder:
                await self._client(
                    functions.messages.UpdateDialogFilterRequest(folder.id)
                )
            return
        used = {item.id for item in result.filters if hasattr(item, "id")}
        folder_id = (
            folder.id
            if folder
            else next(index for index in FOLDER_IDS if index not in used)
        )
        await self._client(
            functions.messages.UpdateDialogFilterRequest(
                folder_id,
                types.DialogFilter(
                    id=folder_id,
                    title=types.TextWithEntities(title, []),
                    pinned_peers=[],
                    include_peers=peers,
                    exclude_peers=[],
                    emoticon=folder.emoticon if folder else None,
                    color=folder.color if folder else None,
                ),
            )
        )

    @translate_errors
    async def delete_channel(self, chat_id: int) -> None:
        try:
            channel = await self._entity(chat_id)
            await self._client(functions.channels.DeleteChannelRequest(channel))
        except ValueError, errors.ChannelInvalidError, errors.ChannelPrivateError:
            logger.warning("Telegram channel {} is already gone", chat_id)

    @translate_errors
    async def check_channel(self, chat_id: int) -> None:
        try:
            channel = await self._entity(chat_id)
        except ValueError as error:
            raise ChannelCheckError(
                f"channel {chat_id} is not available: {error}"
            ) from error
        if not isinstance(channel, types.Channel) or not channel.broadcast:
            raise ChannelCheckError(f"{chat_id} is not a channel")
        if channel.username or channel.usernames:
            raise ChannelCheckError(f"channel {chat_id} is public (has a username)")
        rights = channel.admin_rights
        if not (
            channel.creator
            or (
                rights
                and rights.post_messages
                and rights.delete_messages
                and rights.change_info
            )
        ):
            raise ChannelCheckError(
                f"need admin rights to post, delete messages and change info in {chat_id}"
            )

    @translate_errors
    async def channels(self, chat_ids: list[int]) -> dict[int, ChannelInfo]:
        found = {}
        for chat_id in chat_ids:
            try:
                channel = await self._entity(chat_id)
                full = await self._client(
                    functions.channels.GetFullChannelRequest(channel)
                )
            except errors.FloodWaitError:
                raise
            except (ValueError, errors.RPCError) as error:
                logger.warning("Telegram channel {} is unavailable: {}", chat_id, error)
                continue
            has_photo = not isinstance(channel.photo, types.ChatPhotoEmpty)
            found[chat_id] = ChannelInfo(
                channel.title, full.full_chat.about or "", has_photo
            )
        return found

    @translate_errors
    async def edit_title(self, chat_id: int, title: str) -> None:
        try:
            await self._client(
                functions.channels.EditTitleRequest(await self._entity(chat_id), title)
            )
        except errors.ChatNotModifiedError:
            pass

    @translate_errors
    async def edit_about(self, chat_id: int, about: str) -> None:
        try:
            await self._client(
                functions.messages.EditChatAboutRequest(
                    await self._entity(chat_id), about
                )
            )
        except errors.ChatAboutNotModifiedError:
            pass

    @translate_errors
    async def set_photo(self, chat_id: int, path: Path) -> None:
        uploaded = await self._client.upload_file(str(path))
        photo = types.InputChatUploadedPhoto(file=uploaded)
        await self._client(
            functions.channels.EditPhotoRequest(await self._entity(chat_id), photo)
        )

    @translate_errors
    async def delete_photo(self, chat_id: int) -> None:
        try:
            await self._client(
                functions.channels.EditPhotoRequest(
                    await self._entity(chat_id), types.InputChatPhotoEmpty()
                )
            )
        except errors.ChatNotModifiedError:
            pass

    @translate_errors
    async def photo(self, chat_id: int) -> bytes | None:
        return await self._client.download_profile_photo(
            await self._entity(chat_id), file=bytes
        )

    @translate_errors
    async def send(
        self,
        chat_id: int,
        path: Path,
        *,
        name: str,
        caption: str,
        media: Media,
        as_file: bool,
        thumb: Path | None,
        progress: Callable[[int, int], None],
    ) -> SentFile:
        attributes = [types.DocumentAttributeFilename(name)]
        if not as_file:
            if media.kind == MediaKind.VIDEO:
                attributes.append(
                    types.DocumentAttributeVideo(
                        duration=media.duration or 0,
                        w=media.width,
                        h=media.height,
                        supports_streaming=True,
                    )
                )
            elif media.kind == MediaKind.AUDIO:
                attributes.append(
                    types.DocumentAttributeAudio(
                        duration=int(media.duration or 0),
                        title=media.title,
                        performer=media.performer,
                    )
                )
            elif media.kind == MediaKind.ANIMATION:
                attributes.append(types.DocumentAttributeAnimated())
        handle = await self.upload(path, progress)
        message = await self._client.send_file(
            await self._entity(chat_id),
            handle,
            caption=caption,
            force_document=as_file,
            attributes=attributes,
            thumb=str(thumb) if thumb else None,
            supports_streaming=not as_file,
            silent=True,
        )
        file = message.file
        return SentFile(
            message.id,
            media_size(message.media) or file.size,
            file.mime_type,
            file.width,
            file.height,
        )

    async def upload(
        self, path: Path, progress: Callable[[int, int], None]
    ) -> types.TypeInputFile:
        size = path.stat().st_size
        parts = max(1, -(-size // CHUNK))
        big = size > BIG_FILE
        file_id = helpers.generate_random_long()
        queue = iter(range(parts))
        done = 0
        descriptor = os.open(path, os.O_RDONLY)

        async def worker() -> None:
            nonlocal done
            for part in queue:
                data = os.pread(descriptor, CHUNK, part * CHUNK)
                if big:
                    request = functions.upload.SaveBigFilePartRequest(
                        file_id, part, parts, data
                    )
                else:
                    request = functions.upload.SaveFilePartRequest(file_id, part, data)
                if not await self._client(request):
                    raise TelegramError(
                        f"Telegram did not accept part {part} of {path.name}"
                    )
                done += len(data)
                progress(done, size)

        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(min(self.upload_workers if big else 1, parts)):
                    group.create_task(worker())
        except* Exception as failures:
            raise failures.exceptions[0] from None
        finally:
            os.close(descriptor)
        if big:
            return types.InputFileBig(file_id, parts, path.name)
        return types.InputFile(file_id, parts, path.name, md5_checksum="")

    @translate_errors
    async def message_media(self, chat_id: int, message_id: int) -> tuple[object, int]:
        message = await self._client.get_messages(
            await self._entity(chat_id), ids=message_id
        )
        if not message or not message.media:
            raise TelegramMessageGoneError("telegram message with this file is gone")
        return message.media, media_size(message.media) or message.file.size

    @translate_errors
    async def thumbnail(self, media) -> bytes | None:
        if isinstance(media, types.MessageMediaPhoto):
            sizes = media.photo.sizes if isinstance(media.photo, types.Photo) else []
        elif isinstance(media, types.MessageMediaDocument):
            sizes = getattr(media.document, "thumbs", None) or []
        else:
            sizes = []
        sizes = sorted(
            (
                size
                for size in sizes
                if isinstance(
                    size,
                    (
                        types.PhotoSize,
                        types.PhotoCachedSize,
                        types.PhotoSizeProgressive,
                    ),
                )
            ),
            key=lambda size: size.w * size.h,
        )
        if not sizes:
            return None
        fitting = [size for size in sizes if max(size.w, size.h) <= THUMB_SIDE]
        best = fitting[-1] if fitting else sizes[0]
        return await self._client.download_media(media, file=bytes, thumb=best.type)

    @translate_errors
    async def iter_media(self, media, offset: int, length: int) -> AsyncIterator[bytes]:
        start = offset - offset % CHUNK
        skip = offset - start
        location, extra = download_location(media)
        async for part in self._client.iter_download(
            location, offset=start, request_size=CHUNK, **extra
        ):
            part = part[skip:]
            skip = 0
            if len(part) >= length:
                yield part[:length]
                return
            length -= len(part)
            yield part

    @translate_errors
    async def delete(self, chat_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return
        entity = await self._entity(chat_id)
        for start in range(0, len(message_ids), BATCH):
            await self._client.delete_messages(
                entity, message_ids[start : start + BATCH]
            )
