from types import SimpleNamespace

from telethon import events

from src.infrastructure.integrations.telegram import client as telegram


class FakeTelethon:
    def __init__(self) -> None:
        self.handlers = []
        self.caught_up = False

    def add_event_handler(self, handler, event) -> None:
        self.handlers.append((handler, event))

    async def catch_up(self) -> None:
        assert self.handlers, "catch up must run after the handler is registered"
        self.caught_up = True


async def test_watch_deletions_passes_channel_deletes():
    fake = FakeTelethon()
    client = telegram.TelegramClient.__new__(telegram.TelegramClient)
    client._client = fake
    seen = []

    async def callback(chat_id, message_ids):
        seen.append((chat_id, message_ids))

    await client.watch_deletions(callback)
    handler, event = fake.handlers[0]
    assert isinstance(event, events.MessageDeleted) and fake.caught_up
    await handler(SimpleNamespace(chat_id=-1001, deleted_ids=(5, 6)))
    await handler(SimpleNamespace(chat_id=None, deleted_ids=[7]))
    assert seen == [(-1001, [5, 6])]
