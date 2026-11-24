import asyncio
from types import SimpleNamespace

import pytest
from telethon import errors, types

from src.infrastructure.integrations import (
    TelegramChannelsLimitError,
    TelegramMessageGoneError,
)
from src.infrastructure.integrations.telegram import client as telegram
from src.infrastructure.integrations.telegram.exception import ChannelCheckError
from src.infrastructure.integrations.telegram.login import TelegramLogin


class FakeTelethon:
    def __init__(self) -> None:
        self.up = False
        self.authorized = True
        self.bot = False
        self.calls: list = []
        self.deleted: list[list[int]] = []
        self.unknown = 1
        self.dialogs = 0
        self.logged_out = True
        self.raise_on_call: Exception | None = None
        self.message = None
        self.session = SimpleNamespace(filename="fake.session")

    def is_connected(self) -> bool:
        return self.up

    async def connect(self) -> None:
        self.up = True

    async def disconnect(self) -> None:
        self.up = False

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_me(self):
        return SimpleNamespace(id=7, bot=self.bot, premium=False, username="me")

    async def log_out(self) -> bool:
        return self.logged_out

    async def get_dialogs(self):
        self.dialogs += 1

    async def get_input_entity(self, chat_id):
        if self.unknown:
            self.unknown -= 1
            raise ValueError("unknown peer")
        return f"peer:{chat_id}"

    async def get_entity(self, chat_id):
        if self.unknown:
            self.unknown -= 1
            raise ValueError("unknown entity")
        return self.message

    async def delete_messages(self, entity, ids):
        self.deleted.append(list(ids))

    async def get_messages(self, entity, ids):
        return self.message

    async def __call__(self, request):
        self.calls.append(request)
        if self.raise_on_call:
            raise self.raise_on_call
        return True


def make(fake: FakeTelethon) -> telegram.TelegramClient:
    client = telegram.TelegramClient.__new__(telegram.TelegramClient)
    client._client = fake
    client._wanted = True
    client._pinged = None
    client.authorized = False
    fake.up = True
    return client


async def test_start_reports_authorization_and_refuses_bots():
    fake = FakeTelethon()
    client = make(fake)
    assert await client.start() is True and client._me_id == 7
    fake.authorized = False
    assert await client.start() is False and not client.authorized
    fake.authorized, fake.bot = True, True
    with pytest.raises(RuntimeError, match="user account"):
        await client.start()


async def test_ping_needs_authorization_caches_and_reports_failures(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(telegram.time, "monotonic", lambda: now[0])
    fake = FakeTelethon()
    client = make(fake)
    assert await client.ping() is False and fake.calls == []
    client.authorized = True
    assert await client.ping() is True and len(fake.calls) == 1
    assert await client.ping() is True and len(fake.calls) == 1
    now[0] += telegram.PING_TTL + 1
    fake.raise_on_call = ConnectionError("down")
    assert await client.ping() is False


async def test_log_out_falls_back_to_forgetting_the_session(tmp_path):
    fake = FakeTelethon()
    session = tmp_path / "fake.session"
    session.write_bytes(b"x")
    fake.session.filename = str(session)
    client = make(fake)
    client.authorized, fake.logged_out = True, False
    await client.log_out()
    assert not session.exists() and not client.authorized and not client._wanted
    assert not fake.up


async def test_delete_resolves_the_peer_and_splits_batches():
    fake = FakeTelethon()
    client = make(fake)
    await client.delete(1, [])
    assert fake.deleted == [] and fake.dialogs == 0
    ids = list(range(telegram.BATCH + 5))
    await client.delete(1, ids)
    assert fake.dialogs == 1
    assert fake.deleted == [ids[: telegram.BATCH], ids[telegram.BATCH :]]


async def test_delete_channel_that_is_gone_is_only_logged():
    fake = FakeTelethon()
    client = make(fake)
    fake.unknown = 2
    await client.delete_channel(5)
    assert fake.calls == []
    fake.raise_on_call = errors.ChannelPrivateError(request=None)
    await client.delete_channel(5)
    assert len(fake.calls) == 1


async def test_message_media_of_a_missing_message_is_gone():
    fake = FakeTelethon()
    client = make(fake)
    with pytest.raises(TelegramMessageGoneError):
        await client.message_media(1, 2)
    fake.message = SimpleNamespace(media=None)
    with pytest.raises(TelegramMessageGoneError):
        await client.message_media(1, 2)


async def test_too_many_channels_reports_the_limit():
    fake = FakeTelethon()
    client = make(fake)
    fake.raise_on_call = errors.ChannelsTooMuchError(request=None)
    with pytest.raises(TelegramChannelsLimitError) as caught:
        await client.create_channel("t", "a")
    assert caught.value.extra["limit"] == telegram.CHANNELS_MAX


async def test_not_modified_edits_are_fine():
    fake = FakeTelethon()
    client = make(fake)
    fake.unknown = 0
    fake.raise_on_call = errors.ChatNotModifiedError(request=None)
    await client.edit_title(1, "t")
    await client.delete_photo(1)
    fake.raise_on_call = errors.ChatAboutNotModifiedError(request=None)
    await client.edit_about(1, "a")
    assert len(fake.calls) == 3


def channel(**values) -> types.Channel:
    return types.Channel(
        id=1,
        title="t",
        photo=types.ChatPhotoEmpty(),
        date=None,
        broadcast=values.pop("broadcast", True),
        **values,
    )


@pytest.mark.parametrize(
    "found,message",
    [
        (None, "is not a channel"),
        (channel(broadcast=False), "is not a channel"),
        (channel(creator=True, username="pub"), "public"),
        (channel(), "admin rights"),
        (
            channel(
                admin_rights=types.ChatAdminRights(post_messages=True, change_info=True)
            ),
            "admin rights",
        ),
    ],
)
async def test_check_channel_rejects_unsuitable_chats(found, message):
    fake = FakeTelethon()
    fake.unknown = 0
    fake.message = found
    with pytest.raises(ChannelCheckError, match=message):
        await make(fake).check_channel(1)


async def test_check_channel_accepts_admin_and_reports_unknown():
    fake = FakeTelethon()
    fake.unknown = 0
    rights = types.ChatAdminRights(
        post_messages=True, delete_messages=True, change_info=True
    )
    fake.message = channel(admin_rights=rights)
    await make(fake).check_channel(1)
    fake.unknown = 2
    with pytest.raises(ChannelCheckError, match="not available"):
        await make(fake).check_channel(1)


class FakeQr:
    url = "tg://login?token=x"
    expires = None

    def __init__(self, timeout: bool) -> None:
        self.timeout = timeout

    async def wait(self):
        if self.timeout:
            raise asyncio.TimeoutError


class FakeLoginTelethon(FakeTelethon):
    def __init__(self) -> None:
        super().__init__()
        self.signed: list = []
        self.codes: list = []

    async def qr_login(self):
        return FakeQr(timeout=bool(self.signed))

    async def send_code_request(self, phone):
        self.codes.append(phone)

    async def sign_in(self, phone=None, code=None, password=None):
        self.signed.append((phone, code, password))


async def test_login_steps_and_session_file(tmp_path):
    from src.infrastructure.integrations import TelegramLoginExpiredError

    target = tmp_path / "upload"
    login = TelegramLogin(target, 1, "hash", "tcloud")
    fake = login._client = FakeLoginTelethon()
    url, _ = await login.qr()
    assert url.startswith("tg://") and fake.up
    await login.wait_qr()
    await login.send_code("+100")
    await login.sign_in("12345")
    await login.password("secret")
    assert fake.codes == ["+100"]
    assert fake.signed == [("+100", "12345", None), (None, None, "secret")]
    await login.qr()
    with pytest.raises(TelegramLoginExpiredError):
        await login.wait_qr()
    login._temp.write_bytes(b"session")
    await login.finish()
    final = tmp_path / "upload.session"
    assert final.read_bytes() == b"session" and not login._temp.exists()
    assert final.stat().st_mode & 0o777 == 0o600
    other = TelegramLogin(final, 1, "hash", "tcloud")
    other._client = FakeLoginTelethon()
    other._temp.write_bytes(b"half")
    await other.close()
    assert not other._temp.exists() and final.exists()
