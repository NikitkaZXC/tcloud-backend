"""Telegram через Telethon (MTProto, пользовательский аккаунт).

file_id не храним: он меняется, а file_reference протухает. Храним chat_id + message_id
и при скачивании запрашиваем сообщение заново.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient, errors, functions, helpers, types, utils

from . import limits
from .errors import TCloudError
from .media import Media

log = logging.getLogger(__name__)
CHUNK = 512 * 1024  # кратно 4 КБ, как требует upload.getFile; все куски, кроме последнего, одного размера
BIG_FILE = 10 * 1024 * 1024  # больше — saveBigFilePart, куски можно слать в любом порядке

# Сбои, после которых имеет смысл повторить запрос
TRANSIENT = (ConnectionError, OSError, TimeoutError, errors.ServerError, errors.TimedOutError,
             errors.RpcCallFailError, errors.FilePartMissingError)


@dataclass
class ChannelInfo:
    title: str
    about: str
    has_photo: bool


@dataclass
class Sent:
    message_id: int
    size: int
    mime: str | None
    width: int | None
    height: int | None


class Telegram:
    def __init__(self, session: Path, api_id: int, api_hash: str, upload_workers: int = 4):
        self.upload_workers = upload_workers
        session.parent.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(
            str(session), api_id, api_hash,
            flood_sleep_threshold=limits.FLOOD_WAIT_MAX,  # короткий FloodWait Telethon переждёт сам
            device_model="TCloud",
            receive_updates=False,
        )

    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram session is not authorized: run `docker compose run --rm app python -m tcloud login`")
        me = await self.client.get_me()
        if me.bot:
            raise RuntimeError("TCloud needs a user account: bots cannot create channels")
        log.info("telegram: logged in as %s (premium=%s)", me.username or me.id, bool(me.premium))

    async def stop(self) -> None:
        await self.client.disconnect()

    def connected(self) -> bool:
        return self.client.is_connected()

    async def is_premium(self) -> bool:
        return bool((await self.client.get_me()).premium)

    async def _entity(self, chat_id: int):
        try:
            return await self.client.get_entity(chat_id)
        except ValueError:  # access_hash ещё нет в кеше сессии — прогреваем через диалоги
            await self.client.get_dialogs()
            return await self.client.get_entity(chat_id)

    async def create_channel(self, title: str, about: str) -> int:
        # Канал без username и без инвайт-ссылок — приватный, доступ только у нас
        r = await self.client(functions.channels.CreateChannelRequest(title=title, about=about, broadcast=True))
        return utils.get_peer_id(r.chats[0])

    async def check_channel(self, chat_id: int) -> None:
        ch = await self._entity(chat_id)
        if not isinstance(ch, types.Channel) or not ch.broadcast:
            raise ValueError("not a channel")
        if ch.username or ch.usernames:
            raise ValueError("channel is public (has a username)")
        r = ch.admin_rights
        if not (ch.creator or (r and r.post_messages and r.delete_messages and r.change_info)):
            raise ValueError("need admin rights to post, delete messages and change channel info")

    async def channels(self, chat_ids: list[int]) -> dict[int, ChannelInfo]:
        """Название, описание и фото из Telegram. Недоступные каналы в ответ не попадают."""
        out = {}
        for cid in chat_ids:
            try:
                ch = await self._entity(cid)
                full = await self.client(functions.channels.GetFullChannelRequest(ch))
            except errors.FloodWaitError:
                raise
            except (ValueError, errors.RPCError) as e:
                log.warning("telegram: channel %s is unavailable: %s", cid, e)
                continue
            out[cid] = ChannelInfo(ch.title, full.full_chat.about or "", not isinstance(ch.photo, types.ChatPhotoEmpty))
        return out

    async def edit_title(self, chat_id: int, title: str) -> None:
        try:
            await self.client(functions.channels.EditTitleRequest(await self._entity(chat_id), title))
        except errors.ChatNotModifiedError:
            pass

    async def edit_about(self, chat_id: int, about: str) -> None:
        try:
            await self.client(functions.messages.EditChatAboutRequest(await self._entity(chat_id), about))
        except errors.ChatAboutNotModifiedError:
            pass

    async def set_photo(self, chat_id: int, path: Path) -> None:
        f = await self.client.upload_file(str(path))
        await self.client(functions.channels.EditPhotoRequest(await self._entity(chat_id), types.InputChatUploadedPhoto(file=f)))

    async def delete_photo(self, chat_id: int) -> None:
        try:
            await self.client(functions.channels.EditPhotoRequest(await self._entity(chat_id), types.InputChatPhotoEmpty()))
        except errors.ChatNotModifiedError:
            pass

    async def photo(self, chat_id: int) -> bytes | None:
        return await self.client.download_profile_photo(await self._entity(chat_id), file=bytes)

    async def send(
        self, chat_id: int, path: Path, *, name: str, caption: str, media: Media, as_file: bool,
        thumb: Path | None, progress: Callable[[int, int], None],
    ) -> Sent:
        # Атрибуты передаём сами: без них Telegram не знает размеров и длительности, и видео «плывёт»
        attrs = [types.DocumentAttributeFilename(name)]
        if not as_file:
            if media.kind == "video":
                attrs.append(types.DocumentAttributeVideo(duration=media.duration or 0, w=media.width, h=media.height, supports_streaming=True))
            elif media.kind == "audio":
                attrs.append(types.DocumentAttributeAudio(duration=int(media.duration or 0), title=media.title, performer=media.performer))
            elif media.kind == "animation":
                attrs.append(types.DocumentAttributeAnimated())
        handle = await self.upload(path, progress)
        msg = await self.client.send_file(
            await self._entity(chat_id), handle, caption=caption, force_document=as_file, attributes=attrs,
            thumb=str(thumb) if thumb else None, supports_streaming=not as_file, silent=True,
        )
        f = msg.file
        return Sent(msg.id, f.size, f.mime_type, f.width, f.height)

    async def upload(self, path: Path, progress: Callable[[int, int], None]) -> types.TypeInputFile:
        """Загрузка кусков параллельно: Telethon шлёт их по одному и упирается в задержку сети, а не в канал."""
        size = path.stat().st_size
        parts = max(1, -(-size // CHUNK))
        big = size > BIG_FILE
        file_id = helpers.generate_random_long()
        queue = iter(range(parts))  # общий итератор: каждый кусок берёт ровно один воркер
        done = 0
        fd = os.open(path, os.O_RDONLY)

        async def worker() -> None:
            nonlocal done
            for i in queue:
                data = os.pread(fd, CHUNK, i * CHUNK)
                if big:
                    ok = await self.client(functions.upload.SaveBigFilePartRequest(file_id, i, parts, data))
                else:
                    ok = await self.client(functions.upload.SaveFilePartRequest(file_id, i, data))
                if not ok:
                    raise TCloudError(502, "telegram_error", f"Telegram did not accept part {i} of {path.name}")
                done += len(data)
                progress(done, size)

        try:
            # Файлу ≤ 10 МБ (≤ 20 кусков) параллельность почти ничего не даёт — один воркер, как у Telethon
            async with asyncio.TaskGroup() as tg:
                for _ in range(min(self.upload_workers if big else 1, parts)):
                    tg.create_task(worker())
        except* Exception as eg:
            raise eg.exceptions[0] from None
        finally:
            os.close(fd)
        # Имя с настоящим расширением: по нему Telethon решает, фото это или документ
        if big:
            return types.InputFileBig(file_id, parts, path.name)
        return types.InputFile(file_id, parts, path.name, md5_checksum="")

    async def message_media(self, chat_id: int, message_id: int):
        msg = await self.client.get_messages(await self._entity(chat_id), ids=message_id)
        if not msg or not msg.media:
            raise TCloudError(404, "file_not_found", "telegram message with this file is gone")
        return msg.media

    async def iter_media(self, media, offset: int, length: int) -> AsyncIterator[bytes]:
        start = offset - offset % CHUNK  # смещение запроса должно быть кратно размеру куска
        skip = offset - start
        async for part in self.client.iter_download(media, offset=start, request_size=CHUNK):
            part = part[skip:]
            skip = 0
            if len(part) >= length:
                yield part[:length]
                return
            length -= len(part)
            yield part

    async def delete(self, chat_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return
        ent = await self._entity(chat_id)
        for i in range(0, len(message_ids), limits.BATCH):
            await self.client.delete_messages(ent, message_ids[i:i + limits.BATCH])
