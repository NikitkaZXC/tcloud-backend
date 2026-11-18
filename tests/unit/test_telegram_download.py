from types import SimpleNamespace

import pytest
from telethon import errors, types

from src.infrastructure.enums import MediaKind
from src.infrastructure.integrations.telegram import TelegramFloodWaitError
from src.infrastructure.integrations.telegram import client as telegram
from src.infrastructure.integrations.telegram.client import (
    download_location,
    media_size,
    photo_size,
)
from src.infrastructure.media import Media

SIZES = [
    types.PhotoStrippedSize("i", b"\x01\x08\x08"),
    types.PhotoSize("m", 320, 240, 4000),
    types.PhotoSize("x", 800, 600, 12522),
    types.PhotoSizeProgressive("y", 1280, 960, [900, 5000, 12082]),
]


def photo_media(sizes=SIZES) -> types.MessageMediaPhoto:
    return types.MessageMediaPhoto(
        photo=types.Photo(
            id=7,
            access_hash=8,
            file_reference=b"ref",
            date=None,
            sizes=sizes,
            dc_id=4,
        )
    )


def document_media(size: int) -> types.MessageMediaDocument:
    return types.MessageMediaDocument(
        document=types.Document(
            id=1,
            access_hash=2,
            file_reference=b"ref",
            date=None,
            mime_type="application/pdf",
            size=size,
            dc_id=2,
            attributes=[],
        )
    )


class FakeTelethon:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.calls = []
        self.fail = None

    def iter_download(self, file, *, offset, request_size, **extra):
        self.calls.append((file, offset, extra))

        async def chunks():
            for start in range(offset, len(self.data), request_size):
                yield self.data[start : start + request_size]
            if self.fail:
                raise self.fail

        return chunks()

    async def get_messages(self, entity, ids):
        return self.message

    async def get_entity(self, chat_id):
        return chat_id


def _client(fake: FakeTelethon) -> telegram.TelegramClient:
    client = telegram.TelegramClient.__new__(telegram.TelegramClient)
    client._client = fake
    return client


def test_heaviest_photo_size_is_chosen_even_when_not_last():
    assert photo_size(SIZES).type == "x"
    assert media_size(photo_media()) == 12522


def test_progressive_size_counts_its_full_length():
    sizes = [types.PhotoSize("m", 320, 240, 4000)] + SIZES[3:]
    assert photo_size(sizes).type == "y"
    assert media_size(photo_media(sizes)) == 12082


def test_stripped_and_cached_sizes_are_never_downloaded():
    sizes = [
        types.PhotoStrippedSize("i", b"\x01" * 50),
        types.PhotoCachedSize("s", 90, 90, b"x" * 100000),
    ]
    assert photo_size(sizes) is None
    media = photo_media(sizes)
    assert download_location(media) == (media, {})


def test_photo_location_points_at_the_counted_size():
    location, extra = download_location(photo_media())
    assert isinstance(location, types.InputPhotoFileLocation)
    assert (location.id, location.access_hash, location.thumb_size) == (7, 8, "x")
    assert location.file_reference == b"ref"
    assert extra == {"dc_id": 4, "file_size": 12522}


def test_document_size_and_location_are_untouched():
    media = document_media(777)
    assert media_size(media) == 777
    assert download_location(media) == (media, {})


async def test_iter_media_streams_the_counted_photo_size():
    fake = FakeTelethon(bytes(range(256)) * 60)
    parts = [
        part
        async for part in _client(fake).iter_media(
            photo_media(), telegram.CHUNK + 10, 100
        )
    ]
    assert b"".join(parts) == fake.data[telegram.CHUNK + 10 : telegram.CHUNK + 110]
    location, offset, extra = fake.calls[0]
    assert location.thumb_size == "x" and offset == telegram.CHUNK
    assert extra == {"dc_id": 4, "file_size": 12522}


async def test_iter_media_translates_telethon_errors_mid_stream():
    fake = FakeTelethon(b"x" * telegram.CHUNK)
    fake.fail = errors.FloodWaitError(request=None, capture=30)
    parts = []
    with pytest.raises(TelegramFloodWaitError) as raised:
        async for part in _client(fake).iter_media(document_media(10**9), 0, 10**9):
            parts.append(part)
    assert parts == [fake.data] and raised.value.seconds == 30


async def test_message_media_reports_the_downloaded_photo_size():
    fake = FakeTelethon(b"")
    media = photo_media()
    fake.message = SimpleNamespace(
        media=media, file=SimpleNamespace(size=99999, mime_type="image/jpeg")
    )
    assert await _client(fake).message_media(-100, 5) == (media, 12522)


async def test_send_records_the_downloaded_photo_size(tmp_path, monkeypatch):
    fake = FakeTelethon(b"")
    sent = SimpleNamespace(
        id=11,
        media=photo_media(),
        file=SimpleNamespace(
            size=99999, mime_type="image/jpeg", width=1280, height=960
        ),
    )

    async def send_file(*args, **kwargs):
        return sent

    async def upload(path, progress):
        return "handle"

    fake.send_file = send_file
    client = _client(fake)
    monkeypatch.setattr(client, "upload", upload)
    target = tmp_path / "images.jpeg"
    target.write_bytes(b"jpeg")
    result = await client.send(
        -100,
        target,
        name="images.jpeg",
        caption="#tc",
        media=Media(kind=MediaKind.PHOTO, mime="image/jpeg", width=1280, height=960),
        as_file=False,
        thumb=None,
        progress=lambda done, total: None,
    )
    assert (result.message_id, result.size) == (11, 12522)


async def test_channels_used_counts_channels_and_supergroups():
    fake = FakeTelethon(b"")

    def iter_dialogs():
        async def dialogs():
            for is_channel in (True, False, True, True, False):
                yield SimpleNamespace(is_channel=is_channel)

        return dialogs()

    fake.iter_dialogs = iter_dialogs
    assert await _client(fake).channels_used() == 3
