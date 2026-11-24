import asyncio

import pytest
from telethon import errors, functions, types

from src.infrastructure.integrations.telegram import (
    TelegramError,
    TelegramFileReferenceError,
    TelegramFloodWaitError,
    TelegramUnavailableError,
)
from src.infrastructure.integrations.telegram import client as telegram
from src.infrastructure.integrations.telegram.client import translate_errors


class FakeTelethon:
    def __init__(self, delay: float = 0.005, fail_part: int | None = None) -> None:
        self.parts: dict[int, bytes] = {}
        self.delay, self.fail_part = delay, fail_part
        self.active = self.max_active = 0
        self.requests = []

    async def __call__(self, request):
        self.requests.append(type(request))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if request.file_part == self.fail_part:
                raise ConnectionError("boom")
            self.parts[request.file_part] = request.bytes
            return True
        finally:
            self.active -= 1


def _client(fake: FakeTelethon, workers: int = 4) -> telegram.TelegramClient:
    client = telegram.TelegramClient.__new__(telegram.TelegramClient)
    client._client, client.upload_workers = fake, workers
    return client


async def test_big_file_parts_uploaded_in_parallel_once(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram, "BIG_FILE", 1024 * 1024)
    data = bytes(range(256)) * (13 * 1024)
    target = tmp_path / "v.mp4"
    target.write_bytes(data)
    fake = FakeTelethon()
    progress = []
    handle = await _client(fake).upload(
        target, lambda done, total: progress.append((done, total))
    )
    assert (
        isinstance(handle, types.InputFileBig)
        and handle.parts == 7
        and handle.name == "v.mp4"
    )
    assert b"".join(fake.parts[index] for index in range(7)) == data
    assert set(fake.requests) == {functions.upload.SaveBigFilePartRequest}
    assert fake.max_active == 4
    assert progress[-1] == (len(data), len(data))


async def test_small_file_single_worker(tmp_path):
    target = tmp_path / "a.jpg"
    target.write_bytes(b"x" * (telegram.CHUNK * 2 + 10))
    fake = FakeTelethon()
    handle = await _client(fake).upload(target, lambda *a: None)
    assert isinstance(handle, types.InputFile) and handle.parts == 3
    assert fake.max_active == 1
    assert set(fake.requests) == {functions.upload.SaveFilePartRequest}


async def test_empty_file_is_one_part(tmp_path):
    target = tmp_path / "a.jpg"
    target.write_bytes(b"")
    assert (await _client(FakeTelethon()).upload(target, lambda *a: None)).parts == 1


async def test_failed_part_stops_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram, "BIG_FILE", 1024)
    target = tmp_path / "v.mp4"
    target.write_bytes(b"x" * (telegram.CHUNK * 10))
    fake = FakeTelethon(fail_part=2)
    with pytest.raises(ConnectionError):
        await _client(fake).upload(target, lambda *a: None)
    assert len(fake.parts) < 10


@pytest.mark.parametrize(
    "raised,expected",
    [
        (errors.FloodWaitError(request=None, capture=42), TelegramFloodWaitError),
        (ConnectionError("down"), TelegramUnavailableError),
        (errors.ServerError(request=None, message="500"), TelegramUnavailableError),
        (errors.ChatAdminRequiredError(request=None), TelegramError),
        (errors.FileReferenceExpiredError(request=None), TelegramFileReferenceError),
        (errors.FileReferenceInvalidError(request=None), TelegramFileReferenceError),
    ],
)
async def test_errors_translated(raised, expected):
    @translate_errors
    async def call():
        raise raised

    with pytest.raises(expected) as caught:
        await call()
    if expected is TelegramFloodWaitError:
        assert (
            caught.value.seconds == 42 and caught.value.to_dict()["retry_after"] == 42
        )


class ThumbTelethon:
    def __init__(self) -> None:
        self.thumbs = []

    async def download_media(self, media, file, thumb):
        assert file is bytes
        self.thumbs.append(thumb)
        return b"jpeg"


def _thumb_client() -> tuple[telegram.TelegramClient, ThumbTelethon]:
    client = telegram.TelegramClient.__new__(telegram.TelegramClient)
    client._client = ThumbTelethon()
    return client, client._client


def _photo(sizes) -> types.MessageMediaPhoto:
    return types.MessageMediaPhoto(
        photo=types.Photo(
            id=1,
            access_hash=1,
            file_reference=b"",
            date=None,
            sizes=sizes,
            dc_id=2,
        )
    )


async def test_thumbnail_picks_largest_size_within_limit():
    client, fake = _thumb_client()
    media = _photo(
        [
            types.PhotoStrippedSize(type="i", bytes=b"\x01\x10\x10"),
            types.PhotoSize(type="s", w=90, h=67, size=1000),
            types.PhotoSize(type="m", w=320, h=240, size=9000),
            types.PhotoSizeProgressive(type="y", w=1280, h=960, sizes=[1, 2]),
        ]
    )
    assert await client.thumbnail(media) == b"jpeg"
    assert fake.thumbs == ["m"]


async def test_thumbnail_falls_back_to_smallest_and_skips_stripped():
    client, fake = _thumb_client()
    big = _photo([types.PhotoSizeProgressive(type="y", w=1280, h=960, sizes=[1])])
    assert await client.thumbnail(big) == b"jpeg" and fake.thumbs == ["y"]
    stripped = _photo([types.PhotoStrippedSize(type="i", bytes=b"\x01")])
    assert await client.thumbnail(stripped) is None
    document = types.MessageMediaDocument(
        document=types.Document(
            id=1,
            access_hash=1,
            file_reference=b"",
            date=None,
            mime_type="application/pdf",
            size=10,
            dc_id=2,
            attributes=[],
        )
    )
    assert await client.thumbnail(document) is None
    assert fake.thumbs == ["y"]
