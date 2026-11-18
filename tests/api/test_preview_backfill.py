import asyncio
import io

import pytest
from PIL import Image
from sqlalchemy import text

from src.infrastructure.cache import TaskStore
from src.infrastructure.integrations import TelegramFloodWaitError
from tests.api.conftest import path, upload
from tests.factories import PDF, make_image, make_video


async def drop_previews(engine) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("UPDATE files SET thumbnail = NULL"))


async def previews(engine) -> dict[str, bool]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT name, thumbnail IS NOT NULL FROM files")
        )
    return dict(rows.all())


async def backfill(client, redis, engine, expected: dict[str, bool]) -> None:
    response = await client.post("/files/previews/backfill")
    assert response.status_code == 202, response.text
    for _ in range(200):
        if await previews(engine) == expected and not await redis.keys("*:job:*"):
            return
        await asyncio.sleep(0.05)
    assert await previews(engine) == expected


async def test_backfill_builds_missing_previews(
    client, telegram, storage, engine, redis
):
    photo = (
        await upload(
            client,
            path(storage, "s.png"),
            make_image("PNG", (1280, 960)),
            encrypt="true",
        )
    )["file"]
    gone = (
        await upload(client, path(storage, "gone.jpg"), make_image(), encrypt="true")
    )["file"]
    await upload(client, path(storage, "doc.pdf"), PDF, encrypt="true")
    await upload(client, path(storage, "open.jpg"), make_image(), encrypt="false")
    await drop_previews(engine)
    telegram.chats[gone["telegram"]["chat_id"]]["messages"].pop(
        gone["telegram"]["message_id"]
    )
    assert (await client.get(f"/files/{photo['uuid']}/thumb")).status_code == 404
    await backfill(
        client,
        redis,
        engine,
        {"s.png": True, "gone.jpg": False, "doc.pdf": False, "open.jpg": False},
    )
    response = await client.get(f"/files/{photo['uuid']}/thumb")
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.format == "JPEG" and image.size == (320, 240)
    assert await redis.get(f"tcloud:activity:{storage['id']}:read")
    listed = (await client.get(f"/storages/{storage['id']}")).json()
    assert listed["preview_count"] == 1


async def test_backfill_waits_out_flood_and_keeps_existing(
    client, telegram, storage, engine, redis, monkeypatch
):
    await upload(client, path(storage, "a.jpg"), make_image(), encrypt="true")
    await upload(client, path(storage, "b.jpg"), make_image(), encrypt="true")
    async with engine.connect() as connection:
        kept = (
            await connection.execute(
                text("SELECT thumbnail FROM files WHERE name = 'b.jpg'")
            )
        ).scalar_one()
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE files SET thumbnail = NULL WHERE name = 'a.jpg'")
        )
    original = telegram.message_media
    calls = []

    async def flaky(chat_id, message_id):
        calls.append(message_id)
        if len(calls) == 1:
            raise TelegramFloodWaitError(1)
        return await original(chat_id, message_id)

    monkeypatch.setattr(telegram, "message_media", flaky)
    await backfill(client, redis, engine, {"a.jpg": True, "b.jpg": True})
    assert len(calls) == 2 and calls[0] == calls[1]
    async with engine.connect() as connection:
        assert (
            await connection.execute(
                text("SELECT thumbnail FROM files WHERE name = 'b.jpg'")
            )
        ).scalar_one() == kept


@pytest.mark.ffmpeg
async def test_backfill_builds_video_preview(client, storage, engine, redis):
    video = (
        await upload(
            client,
            path(storage, "clip.mp4"),
            make_video("mp4", 640, 360),
            encrypt="true",
        )
    )["file"]
    await drop_previews(engine)
    await backfill(client, redis, engine, {"clip.mp4": True})
    response = await client.get(f"/files/{video['uuid']}/thumb")
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (320, 180)


async def test_backfill_is_root_only(client, make_user):
    admin = await make_user("boss", "admin")
    response = await admin.post("/files/previews/backfill")
    assert response.status_code == 403


async def test_backfill_job_lock(redis):
    store = TaskStore(redis)
    assert await store.hold_job("previews", "a")
    assert not await store.hold_job("previews", "b")
    assert await store.hold_job("previews", "a")
    await store.release_job("previews", "b")
    assert not await store.hold_job("previews", "b")
    await store.release_job("previews", "a")
    assert await store.hold_job("previews", "b")
