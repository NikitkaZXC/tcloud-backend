import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telethon import types

from src.config import settings
from src.infrastructure.enums import MediaKind
from src.infrastructure.integrations import (
    Authorization,
    ChannelCheckError,
    ChannelInfo,
    InfectedFileError,
    ScannerUnavailableError,
    SentFile,
    TelegramChannelsLimitError,
    TelegramCodeInvalidError,
    TelegramFloodWaitError,
    TelegramLoginExpiredError,
    TelegramMessageGoneError,
    TelegramPasswordInvalidError,
    TelegramPasswordNeededError,
    TelegramSessionTooFreshError,
    TelegramUnauthorizedError,
    TelegramUnavailableError,
    UnscannableFileError,
)
from src.infrastructure.integrations.telegram.client import photo_size
from src.infrastructure.integrations.telegram.login import session_file


def photo_sizes(data: bytes) -> list[tuple[types.TypePhotoSize, bytes | None]]:
    small = data[: max(1, len(data) // 3)]
    lighter = data[: max(1, len(data) - 440)]
    return [
        (types.PhotoStrippedSize("i", b"\x01\x08\x08"), b""),
        (types.PhotoSize("m", 320, 240, len(small)), small),
        (types.PhotoSize("x", 800, 600, len(data)), None),
        (types.PhotoSizeProgressive("y", 1280, 960, [16, len(lighter)]), lighter),
    ]


def body(message: dict) -> bytes:
    if "sizes" not in message:
        return message["data"]
    best = photo_size([size for size, _ in message["sizes"]])
    data = next(data for size, data in message["sizes"] if size is best)
    return message["data"] if data is None else data


class FakeTelegram:
    def __init__(self, session: Path | None = None) -> None:
        self.session = session_file(session) if session else None
        self.authorized = True
        self.authorizations_list: list[Authorization] = []
        self.too_fresh = False
        self.channels_full = False
        self.premium = False
        self.chats: dict[int, dict] = {}
        self.next_chat = 1001234567000
        self.next_message = 1
        self.flood: int | None = None
        self.flood_once: int | None = None
        self.fail_times = 0
        self.delay = 0.0
        self.part_delay = 0.0
        self.parts = 2
        self.sent_hook = None
        self.sending = 0
        self.max_sending = 0
        self.on_deleted = None
        self.on_channel = None
        self.on_account = None
        self.other_channels = 0
        self.dialogs_read = 0
        self.dialogs_flood = False
        self.folders: dict[str, list[int]] = {}
        self.folder_fails = False

    async def start(self) -> bool:
        if self.session:
            self.authorized = self.session.exists()
        return self.authorized

    async def stop(self) -> None:
        pass

    async def ensure_connected(self) -> None:
        return None

    async def ping(self) -> bool:
        return self.connected() and self.authorized

    def connected(self) -> bool:
        return True

    def _check(self) -> None:
        if not self.authorized:
            raise TelegramUnauthorizedError("Telegram session is not authorized")

    async def log_out(self) -> None:
        await self.forget()

    async def forget(self) -> None:
        self.authorized = False
        if self.session:
            self.session.unlink(missing_ok=True)

    async def authorizations(self) -> list[Authorization]:
        self._check()
        return list(self.authorizations_list)

    async def revoke(self, authorization_hash: int) -> None:
        if self.too_fresh:
            raise TelegramSessionTooFreshError("session is too fresh")
        self.authorizations_list = [
            item for item in self.authorizations_list if item.hash != authorization_hash
        ]

    def add_authorization(
        self,
        device_model: str,
        age_hours: float,
        current: bool = False,
        api_id: int | None = None,
    ) -> Authorization:
        item = Authorization(
            hash=len(self.authorizations_list) + 2**40,
            device_model=device_model,
            created_at=datetime.now(UTC) - timedelta(hours=age_hours),
            active_at=datetime.now(UTC),
            ip="127.0.0.1",
            country="NL",
            current=current,
            api_id=api_id or settings.telegram.API_ID,
        )
        self.authorizations_list.append(item)
        return item

    async def is_premium(self) -> bool:
        self._check()
        return self.premium

    async def channels_used(self) -> int:
        self._check()
        if self.dialogs_flood:
            raise TelegramFloodWaitError(30)
        self.dialogs_read += 1
        return len(self.chats) + self.other_channels

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
        self._check()
        if self.channels_full:
            raise TelegramChannelsLimitError("too many channels", limit=500)
        chat_id = self.add_channel(title)
        self.chats[chat_id]["about"] = about
        return chat_id

    async def sync_folder(self, title: str, chat_ids: list[int]) -> None:
        self._check()
        if self.folder_fails:
            raise TelegramFloodWaitError(30)
        if chat_ids:
            self.folders[title] = list(chat_ids)
        else:
            self.folders.pop(title, None)

    async def delete_channel(self, chat_id: int) -> None:
        self._check()
        self.chats.pop(chat_id, None)

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
        assert Path(path).suffix in (".jpg", ".png")
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
        for part in range(self.parts + 1):
            progress(len(data) * part // self.parts, len(data))
            await asyncio.sleep(self.part_delay)
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
        compressed = media.kind == MediaKind.PHOTO and not as_file
        if compressed:
            self.chats[chat_id]["messages"][message_id]["sizes"] = photo_sizes(data)
        mime = "image/jpeg" if compressed else media.mime
        if self.sent_hook:
            await self.sent_hook()
        return SentFile(
            message_id,
            len(body(self.chats[chat_id]["messages"][message_id])),
            mime,
            media.width,
            media.height,
        )

    async def message_media(self, chat_id: int, message_id: int) -> tuple[dict, int]:
        await asyncio.sleep(self.delay)
        message = self.chats[chat_id]["messages"].get(message_id)
        if not message:
            raise TelegramMessageGoneError("gone")
        return message, len(body(message))

    async def thumbnail(self, media: dict) -> bytes | None:
        if media["media"].kind == MediaKind.PHOTO:
            return media["data"]
        return media["thumb"]

    async def iter_media(self, media: dict, offset: int, length: int):
        data = body(media)[offset : offset + length]
        for start in range(0, len(data), 7):
            yield data[start : start + 7]

    async def delete(self, chat_id: int, message_ids: list[int]) -> None:
        for message_id in message_ids:
            self.chats[chat_id]["messages"].pop(message_id, None)

    async def watch(self, deleted, edited, channel_changed, account_changed) -> None:
        self.on_deleted = deleted
        self.on_edited = edited
        self.on_channel = channel_changed
        self.on_account = account_changed

    async def delete_by_hand(self, chat_id: int, message_ids: list[int]) -> None:
        for message_id in message_ids:
            self.chats[chat_id]["messages"].pop(message_id, None)
        await self.on_deleted(chat_id, message_ids)

    def messages(self) -> list[dict]:
        return [
            message
            for chat in self.chats.values()
            for message in chat["messages"].values()
        ]


class FakeLogin:
    def __init__(self, session: Path) -> None:
        self.path = session_file(session)
        self.code = "12345"
        self.secret = "correct horse"
        self.two_step = False
        self.scanned = asyncio.Event()
        self.expire = False
        self.phone: str | None = None
        self.closed = False

    async def qr(self) -> tuple[str, datetime]:
        return "tg://login?token=dGNsb3Vk", datetime.now(UTC) + timedelta(seconds=30)

    async def wait_qr(self) -> None:
        await self.scanned.wait()
        if self.expire:
            raise TelegramLoginExpiredError("the QR code expired, request a new one")
        if self.two_step:
            raise TelegramPasswordNeededError("password needed")

    async def send_code(self, phone: str) -> None:
        if phone == "+70000000000":
            raise TelegramFloodWaitError(79200)
        self.phone = phone

    async def sign_in(self, code: str) -> None:
        if code != self.code:
            raise TelegramCodeInvalidError("the login code is wrong or expired")
        if self.two_step:
            raise TelegramPasswordNeededError("password needed")

    async def password(self, password: str) -> None:
        if password != self.secret:
            raise TelegramPasswordInvalidError("wrong password")

    async def finish(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"session")

    async def close(self) -> None:
        self.closed = True


class FakeScanner:
    def __init__(self) -> None:
        self.available = True
        self.delay = 0.0
        self.parts = 2

    async def ping(self) -> bool:
        return self.available

    async def scan(self, path: Path, progress=None) -> None:
        if not self.available:
            raise ScannerUnavailableError("antivirus is not available, upload rejected")
        data = Path(path).read_bytes()
        for part in range(1, self.parts + 1):
            await asyncio.sleep(self.delay)
            if progress:
                progress(len(data) * part // self.parts)
        if b"EICAR" in data:
            raise InfectedFileError("antivirus found Eicar-Signature, file rejected")
        if b"ENCRYPTED" in data:
            raise UnscannableFileError(
                "antivirus cannot check this file fully (Heuristics.Encrypted.Zip)",
                reason="Heuristics.Encrypted.Zip",
            )
