import asyncio
from pathlib import Path

from src.infrastructure.enums import MediaKind
from src.infrastructure.integrations import (
    ChannelCheckError,
    ChannelInfo,
    InfectedFileError,
    ScannerUnavailableError,
    SentFile,
    TelegramFloodWaitError,
    TelegramMessageGoneError,
    TelegramUnavailableError,
    UnscannableFileError,
)


class FakeTelegram:
    def __init__(self) -> None:
        self.premium = False
        self.chats: dict[int, dict] = {}
        self.next_chat = 1001234567000
        self.next_message = 1
        self.flood: int | None = None
        self.flood_once: int | None = None
        self.fail_times = 0
        self.delay = 0.0
        self.sending = 0
        self.max_sending = 0

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def connected(self) -> bool:
        return True

    async def is_premium(self) -> bool:
        return self.premium

    def add_channel(
        self, title: str = "chat", public: bool = False, admin: bool = True
    ) -> int:
        self.next_chat += 1
        chat_id = -self.next_chat
        self.chats[chat_id] = {
            "title": title,
            "about": "",
            "photo": None,
            "public": public,
            "admin": admin,
            "messages": {},
        }
        return chat_id

    async def create_channel(self, title: str, about: str) -> int:
        chat_id = self.add_channel(title)
        self.chats[chat_id]["about"] = about
        return chat_id

    async def check_channel(self, chat_id: int) -> None:
        chat = self.chats.get(chat_id)
        if chat is None:
            raise ChannelCheckError("not found")
        if chat["public"]:
            raise ChannelCheckError("channel is public")
        if not chat["admin"]:
            raise ChannelCheckError("not admin")

    async def channels(self, chat_ids: list[int]) -> dict[int, ChannelInfo]:
        return {
            chat_id: ChannelInfo(
                self.chats[chat_id]["title"],
                self.chats[chat_id]["about"],
                self.chats[chat_id]["photo"] is not None,
            )
            for chat_id in chat_ids
            if chat_id in self.chats
        }

    async def edit_title(self, chat_id: int, title: str) -> None:
        self.chats[chat_id]["title"] = title

    async def edit_about(self, chat_id: int, about: str) -> None:
        self.chats[chat_id]["about"] = about

    async def set_photo(self, chat_id: int, path: Path) -> None:
        self.chats[chat_id]["photo"] = Path(path).read_bytes()

    async def delete_photo(self, chat_id: int) -> None:
        self.chats[chat_id]["photo"] = None

    async def photo(self, chat_id: int) -> bytes | None:
        return self.chats[chat_id]["photo"]

    async def send(
        self, chat_id, path, *, name, caption, media, as_file, thumb, progress
    ) -> SentFile:
        if self.flood:
            raise TelegramFloodWaitError(self.flood)
        if self.flood_once:
            seconds, self.flood_once = self.flood_once, None
            raise TelegramFloodWaitError(seconds)
        if self.fail_times:
            self.fail_times -= 1
            raise TelegramUnavailableError("network is down")
        self.sending += 1
        self.max_sending = max(self.max_sending, self.sending)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.sending -= 1
        data = Path(path).read_bytes()
        for done in (0, len(data) // 2, len(data)):
            progress(done, len(data))
        message_id = self.next_message
        self.next_message += 1
        self.chats[chat_id]["messages"][message_id] = {
            "data": data,
            "caption": caption,
            "name": name,
            "as_file": as_file,
            "media": media,
            "thumb": Path(thumb).read_bytes() if thumb else None,
            "path": str(path),
        }
        mime = (
            "image/jpeg"
            if media.kind == MediaKind.PHOTO and not as_file
            else media.mime
        )
        return SentFile(message_id, len(data), mime, media.width, media.height)

    async def message_media(self, chat_id: int, message_id: int) -> dict:
        message = self.chats[chat_id]["messages"].get(message_id)
        if not message:
            raise TelegramMessageGoneError("gone")
        return message

    async def iter_media(self, media: dict, offset: int, length: int):
        data = media["data"][offset : offset + length]
        for start in range(0, len(data), 7):
            yield data[start : start + 7]

    async def delete(self, chat_id: int, message_ids: list[int]) -> None:
        for message_id in message_ids:
            self.chats[chat_id]["messages"].pop(message_id, None)

    def messages(self) -> list[dict]:
        return [
            message
            for chat in self.chats.values()
            for message in chat["messages"].values()
        ]


class FakeScanner:
    def __init__(self) -> None:
        self.available = True

    async def ping(self) -> bool:
        return self.available

    async def scan(self, path: Path) -> None:
        if not self.available:
            raise ScannerUnavailableError("antivirus is not available, upload rejected")
        data = Path(path).read_bytes()
        if b"EICAR" in data:
            raise InfectedFileError("antivirus found Eicar-Signature, file rejected")
        if b"ENCRYPTED" in data:
            raise UnscannableFileError(
                "antivirus cannot check this file fully (Heuristics.Encrypted.Zip)"
            )
