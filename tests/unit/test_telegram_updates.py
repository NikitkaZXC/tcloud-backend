from types import SimpleNamespace

from telethon import events
from telethon.tl import types

from src.infrastructure.integrations.telegram import client as telegram


class FakeTelethon:
    def __init__(self) -> None:
        self.handlers = []
        self.caught_up = False

    def add_event_handler(self, handler, event) -> None:
        self.handlers.append((handler, event))

    async def catch_up(self) -> None:
        assert len(self.handlers) == 2, "catch up must run after handlers are added"
        self.caught_up = True


async def watched():
    fake = FakeTelethon()
    client = telegram.TelegramClient.__new__(telegram.TelegramClient)
    client._client, client._me_id = fake, 42
    seen = []

    async def deleted(chat_id, message_ids):
        seen.append(("deleted", chat_id, message_ids))

    async def channel(chat_id):
        seen.append(("channel", chat_id))

    async def account():
        seen.append(("account",))

    await client.watch(deleted, channel, account)
    assert fake.caught_up
    return fake.handlers, seen


async def test_watch_passes_channel_deletes():
    handlers, seen = await watched()
    handler, event = handlers[0]
    assert isinstance(event, events.MessageDeleted)
    await handler(SimpleNamespace(chat_id=-1001, deleted_ids=(5, 6)))
    await handler(SimpleNamespace(chat_id=None, deleted_ids=[7]))
    assert seen == [("deleted", -1001, [5, 6])]


def service(action) -> types.UpdateNewChannelMessage:
    return types.UpdateNewChannelMessage(
        types.MessageService(
            id=1, peer_id=types.PeerChannel(777), date=None, action=action
        ),
        pts=1,
        pts_count=1,
    )


async def test_watch_reports_channel_and_account_changes():
    handlers, seen = await watched()
    handler, event = handlers[1]
    assert isinstance(event, events.Raw)
    assert set(event.types) == {
        types.UpdateChannel,
        types.UpdateNewChannelMessage,
        types.UpdateUser,
    }
    await handler(types.UpdateChannel(channel_id=777))
    await handler(service(types.MessageActionChatEditTitle("new")))
    await handler(service(types.MessageActionChatDeletePhoto()))
    await handler(service(types.MessageActionPinMessage()))
    await handler(
        types.UpdateNewChannelMessage(
            types.Message(id=2, peer_id=types.PeerChannel(777), date=None, message="x"),
            pts=2,
            pts_count=1,
        )
    )
    await handler(types.UpdateUser(user_id=7))
    await handler(types.UpdateUser(user_id=42))
    assert seen == [
        ("channel", -1000000000777),
        ("channel", -1000000000777),
        ("channel", -1000000000777),
        ("account",),
    ]
