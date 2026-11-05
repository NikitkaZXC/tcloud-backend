"""Работа с Telegram через MTProto (Pyrogram), как в AnimehaTube-bot.

Bot API не подходит: там лимит 50 МБ на загрузку и 20 МБ на скачивание.
Через MTProto и бот, и обычный аккаунт загружают до 2 ГБ (с Premium до 4 ГБ).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Protocol
from urllib.parse import urlparse

from pyrogram import Client
from pyrogram.types import Message

log = logging.getLogger(__name__)

Progress = Callable[[int, int], Awaitable[None] | None]


class StorageBackend(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def upload(self, path: Path, file_name: str, caption: str, progress: Progress | None = None) -> tuple[int, int]: ...
    def stream(self, chat_id: int, message_id: int) -> AsyncIterator[bytes]: ...
    async def delete(self, chat_id: int, message_ids: list[int]) -> None: ...


def _proxy(url: str | None) -> dict | None:
    if not url:
        return None
    u = urlparse(url)
    proxy = {"scheme": u.scheme, "hostname": u.hostname, "port": u.port}
    if u.username:
        proxy["username"] = u.username
        proxy["password"] = u.password
    return proxy


class TelegramBackend:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        chat_id: int | str,
        workdir: Path,
        bot_token: str | None = None,
        session_string: str | None = None,
        proxy_url: str | None = None,
    ):
        self.chat_id = chat_id
        self.is_bot = bool(bot_token)
        workdir.mkdir(parents=True, exist_ok=True)
        self.client = Client(
            name="tcloud",
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            session_string=session_string,
            workdir=str(workdir),
            proxy=_proxy(proxy_url),
            device_model="TCloud",
            sleep_threshold=120,  # FloodWait до 2 минут Pyrogram переждёт сам
            no_updates=True,  # апдейты нам не нужны, только загрузка/скачивание
        )

    async def start(self) -> None:
        await self.client.start()
        me = await self.client.get_me()
        log.info("telegram: logged in as %s (bot=%s)", me.username or me.id, self.is_bot)
        await self._resolve_chat()

    async def _resolve_chat(self) -> None:
        """Pyrogram должен знать access_hash чата, иначе будет PEER_ID_INVALID."""
        try:
            chat = await self.client.get_chat(self.chat_id)
        except Exception:
            if self.is_bot:
                raise RuntimeError(
                    f"bot cannot access chat {self.chat_id}: add it to the channel as an admin"
                ) from None
            # Пользовательская сессия: прогреваем кеш через список диалогов
            async for _ in self.client.get_dialogs():
                pass
            chat = await self.client.get_chat(self.chat_id)
        log.info("telegram: storage chat is %s (%s)", chat.title or chat.first_name, chat.id)

    async def stop(self) -> None:
        if self.client.is_connected:
            await self.client.stop()

    async def upload(self, path: Path, file_name: str, caption: str, progress: Progress | None = None) -> tuple[int, int]:
        msg: Message = await self.client.send_document(
            chat_id=self.chat_id,
            document=str(path),
            file_name=file_name,
            caption=caption,
            force_document=True,
            disable_notification=True,
            progress=progress,
        )
        return msg.chat.id, msg.id

    async def stream(self, chat_id: int, message_id: int) -> AsyncIterator[bytes]:
        # Сообщение берём заново: file_reference у старых file_id протухает
        msg: Message = await self.client.get_messages(chat_id, message_id)
        if msg is None or msg.empty or msg.document is None:
            raise FileNotFoundError(f"telegram message {chat_id}/{message_id} is gone")
        async for chunk in self.client.stream_media(msg):
            yield chunk

    async def delete(self, chat_id: int, message_ids: list[int]) -> None:
        if message_ids:
            await self.client.delete_messages(chat_id, message_ids)
