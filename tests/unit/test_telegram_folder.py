from telethon import functions, types

from src.infrastructure.integrations.telegram import client as telegram


class FakeFolders:
    def __init__(self, filters: list) -> None:
        self.filters = filters
        self.updates = []

    async def __call__(self, request):
        if isinstance(request, functions.messages.GetDialogFiltersRequest):
            return types.messages.DialogFilters(filters=self.filters)
        self.updates.append(request)
        return True


def _folder_client(fake: FakeFolders, premium: bool = False) -> telegram.TelegramClient:
    client = telegram.TelegramClient.__new__(telegram.TelegramClient)
    client._client = fake

    async def entity(chat_id):
        return types.InputPeerChannel(-chat_id, 0)

    async def is_premium():
        return premium

    client._peer, client.is_premium = entity, is_premium
    return client


def _folder(folder_id: int, title: str) -> types.DialogFilter:
    return types.DialogFilter(
        id=folder_id,
        title=types.TextWithEntities(title, []),
        pinned_peers=[],
        include_peers=[],
        exclude_peers=[],
        emoticon="📁",
    )


async def test_sync_folder_creates_with_first_free_id():
    fake = FakeFolders([types.DialogFilterDefault(), _folder(2, "Work")])
    await _folder_client(fake).sync_folder("TCloud", [-1, -2])
    (update,) = fake.updates
    assert update.id == 3 and update.filter.title.text == "TCloud"
    assert [peer.channel_id for peer in update.filter.include_peers] == [1, 2]


async def test_sync_folder_updates_existing_and_caps_peers(monkeypatch):
    monkeypatch.setattr(telegram, "FOLDER_PEERS_MAX", 2)
    fake = FakeFolders([_folder(7, "TCloud")])
    await _folder_client(fake).sync_folder("TCloud", [-1, -2, -3])
    (update,) = fake.updates
    assert update.id == 7 and update.filter.emoticon == "📁"
    assert len(update.filter.include_peers) == 2


async def test_sync_folder_without_chats_deletes_it():
    fake = FakeFolders([_folder(4, "TCloud")])
    await _folder_client(fake).sync_folder("TCloud", [])
    (update,) = fake.updates
    assert update.id == 4 and update.filter is None
    missing = FakeFolders([_folder(4, "Work")])
    await _folder_client(missing).sync_folder("TCloud", [])
    assert missing.updates == []
