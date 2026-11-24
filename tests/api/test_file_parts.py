import asyncio
import hashlib
import io
import os
import zipfile
from dataclasses import asdict
from uuid import uuid7

import pytest
from sqlalchemy import select

import main
from src.application.services import pipeline
from src.infrastructure.database.models import FileDB, FilePartDB
from src.infrastructure.enums import MediaKind
from src.infrastructure.media import Media
from src.infrastructure.security import HEADER, part_spans
from src.infrastructure.security.encryption import BLOCK, TAG
from tests.api.conftest import build_archive, path, start_upload, upload, wait_task
from tests.api.test_tasks import redeliver, sessions, telegram_ready
from tests.factories import make_image

LIMIT = 1000
ENCRYPTED_LIMIT = HEADER + 2 * (BLOCK + TAG)
PLAIN = os.urandom(2500)
SECRET = os.urandom(BLOCK * 5 + 123)

__all__ = ["sessions"]


@pytest.fixture
def small_parts(monkeypatch):
    def use(limit: int) -> None:
        monkeypatch.setattr(pipeline, "file_limit", lambda premium: limit)

    use(LIMIT)
    return use


async def parts_of(sessions, file_id: str) -> list[tuple[int, int, int, int]]:
    async with sessions() as session:
        rows = await session.scalars(
            select(FilePartDB)
            .where(FilePartDB.file_id == file_id)
            .order_by(FilePartDB.index)
        )
        return [(row.index, row.tg_message_id, row.offset, row.size) for row in rows]


async def stored(client, storage, name: str, data: bytes, **options) -> dict:
    task = await upload(client, path(storage, name), data, **options)
    assert task["status"] == "done", task
    return task


async def ranges_match(client, file_id: str, data: bytes, ranges) -> None:
    whole = await client.get(f"/files/{file_id}/download")
    assert whole.status_code == 200 and whole.content == data
    assert whole.headers["content-length"] == str(len(data))
    for start, end in ranges:
        part = await client.get(
            f"/files/{file_id}/download", headers={"Range": f"bytes={start}-{end}"}
        )
        assert part.status_code == 206 and part.content == data[start : end + 1]
        assert part.headers["content-range"] == f"bytes {start}-{end}/{len(data)}"


async def test_plain_file_is_split_into_parts(
    client, telegram, storage, small_parts, sessions
):
    task = await stored(client, storage, "big.bin", PLAIN, encrypt="false")
    file = task["file"]
    assert file["size"] == len(PLAIN) and file["encrypted"] is False
    assert file["sha256"] == hashlib.sha256(PLAIN).hexdigest()
    assert task["warnings"] == [] and task["bytes_done"] == len(PLAIN)
    messages = telegram.messages()
    assert [message["name"] for message in messages] == [
        f"big.bin.part{index}" for index in range(3)
    ]
    assert all(message["as_file"] for message in messages)
    assert b"".join(message["data"] for message in messages) == PLAIN
    rows = await parts_of(sessions, file["uuid"])
    assert [(index, offset, size) for index, _, offset, size in rows] == [
        (0, 0, 1000),
        (1, 1000, 1000),
        (2, 2000, 500),
    ]
    assert rows[0][1] == file["telegram"]["message_id"]
    await ranges_match(
        client, file["uuid"], PLAIN, ((0, 0), (990, 1010), (500, 2200), (2000, 2499))
    )


async def test_encrypted_file_parts_decrypt_on_their_own(
    client, telegram, storage, small_parts, sessions
):
    small_parts(ENCRYPTED_LIMIT)
    file = (await stored(client, storage, "secret.bin", SECRET))["file"]
    assert file["encrypted"] is True and file["size"] == len(SECRET)
    messages = telegram.messages()
    assert [message["name"] for message in messages] == [
        f"{file['uuid']}.part{index}.bin" for index in range(3)
    ]
    assert all(len(message["data"]) <= ENCRYPTED_LIMIT for message in messages)
    rows = await parts_of(sessions, file["uuid"])
    assert rows[1][2] == HEADER + 2 * (BLOCK + TAG)
    await ranges_match(
        client,
        file["uuid"],
        SECRET,
        (
            (0, 0),
            (2 * BLOCK - 10, 2 * BLOCK + 10),
            (2 * BLOCK, 4 * BLOCK - 1),
            (BLOCK, len(SECRET) - 1),
        ),
    )


async def test_media_too_big_for_one_message_is_sent_as_file(
    client, telegram, storage, small_parts
):
    data = make_image(size=(400, 300))
    small_parts(len(data) // 2 + 1)
    task = await stored(
        client, storage, "photo.jpg", data, send_as="media", encrypt="false"
    )
    assert task["file"]["sent_as"] == "file" and task["file"]["kind"] == "photo"
    assert task["warning_details"][-1]["code"] == "too_big_sent_as_file"
    assert task["warning_details"][-1]["params"] == {"parts": 2}
    assert len(telegram.messages()) == 2
    await ranges_match(client, task["file"]["uuid"], data, ((0, 10),))


async def test_single_part_file_has_no_part_rows(
    client, telegram, storage, small_parts, sessions
):
    file = (await stored(client, storage, "small.bin", PLAIN[:LIMIT], encrypt="false"))[
        "file"
    ]
    assert len(telegram.messages()) == 1
    assert telegram.messages()[0]["name"] == "small.bin"
    assert await parts_of(sessions, file["uuid"]) == []


async def test_delete_removes_every_part(client, telegram, storage, small_parts):
    file = (await stored(client, storage, "a/big.bin", PLAIN, encrypt="false"))["file"]
    assert (await client.delete(f"/files/{file['uuid']}")).status_code == 204
    assert telegram.messages() == []
    other = (await stored(client, storage, "b/big.bin", PLAIN, encrypt="false"))["file"]
    response = await client.delete("/folders", params={"path": path(storage, "b")})
    assert response.json()["deleted_files"] == 1
    assert telegram.messages() == []
    assert (await client.get(f"/files/{other['uuid']}")).status_code == 404


async def test_deleted_later_part_drops_the_file(
    client, telegram, storage, small_parts, sessions
):
    file = (await stored(client, storage, "big.bin", PLAIN, encrypt="false"))["file"]
    rows = await parts_of(sessions, file["uuid"])
    await telegram.delete_by_hand(file["telegram"]["chat_id"], [rows[2][1]])
    assert (await client.get(f"/files/{file['uuid']}")).status_code == 404
    assert telegram.messages() == []
    assert await parts_of(sessions, file["uuid"]) == []


async def test_missing_part_on_download_drops_the_file(
    client, telegram, storage, small_parts, sessions
):
    file = (await stored(client, storage, "big.bin", PLAIN, encrypt="false"))["file"]
    rows = await parts_of(sessions, file["uuid"])
    chat_id = file["telegram"]["chat_id"]
    telegram.chats[chat_id]["messages"].pop(rows[1][1])
    response = await client.get(f"/files/{file['uuid']}/download")
    assert response.status_code == 404
    assert (await client.get(f"/files/{file['uuid']}")).status_code == 404
    assert telegram.messages() == []


async def test_replace_deletes_old_parts(
    client, telegram, storage, small_parts, sessions
):
    first = (await stored(client, storage, "big.bin", PLAIN, encrypt="false"))["file"]
    second = (
        await stored(
            client,
            storage,
            "big.bin",
            PLAIN[:LIMIT],
            encrypt="false",
            on_conflict="replace",
        )
    )["file"]
    assert first["uuid"] == second["uuid"]
    assert [message["data"] for message in telegram.messages()] == [PLAIN[:LIMIT]]
    assert await parts_of(sessions, first["uuid"]) == []
    third = (
        await stored(
            client, storage, "big.bin", PLAIN, encrypt="false", on_conflict="replace"
        )
    )["file"]
    assert len(telegram.messages()) == 3
    assert len(await parts_of(sessions, third["uuid"])) == 3


async def test_archive_reads_every_part(client, telegram, storage, small_parts):
    await stored(client, storage, "a/plain.bin", PLAIN, encrypt="false")
    small_parts(ENCRYPTED_LIMIT)
    await stored(client, storage, "a/secret.bin", SECRET)
    assert len(telegram.messages()) == 6
    task, response = await build_archive(client, path(storage, "a"), format="zip")
    assert task["warnings"] == []
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("plain.bin") == PLAIN
        assert archive.read("secret.bin") == SECRET


async def test_archive_skips_file_with_missing_part(
    client, telegram, storage, small_parts, sessions
):
    file = (await stored(client, storage, "a/big.bin", PLAIN, encrypt="false"))["file"]
    rows = await parts_of(sessions, file["uuid"])
    telegram.chats[file["telegram"]["chat_id"]]["messages"].pop(rows[1][1])
    task, _ = await build_archive(client, path(storage, "a"), format="zip")
    assert [item["code"] for item in task["warning_details"]] == ["archive_file_gone"]


async def test_cancel_deletes_parts_already_sent(
    client, telegram, storage, small_parts
):
    telegram.delay = 0.3
    task_id = await start_upload(
        client, path(storage, "big.bin"), PLAIN, encrypt="false"
    )
    store = main.app.state.runtime.store
    for _ in range(500):
        if ((await store.get(task_id)) or {}).get("sent"):
            break
        await asyncio.sleep(0.01)
    assert len(telegram.messages()) == 1
    await client.post(f"/tasks/{task_id}/cancel")
    task = await wait_task(client, task_id)
    assert task["status"] == "cancelled"
    assert telegram.messages() == []


async def test_redelivery_resumes_after_the_last_sent_part(
    client, telegram, storage, worker, sessions, small_parts
):
    task = await telegram_ready(client, storage, telegram, claimed=True)
    data = task["file"].read_bytes()
    small_parts(len(data) // 3 + 1)
    spans = part_spans(len(data), len(data) // 3 + 1, False)
    assert len(spans) == 3
    file_id = uuid7()
    sent = await telegram.send(
        task["chat_id"],
        task["file"],
        name="crash.jpg.part0",
        caption="x",
        media=Media(mime="image/jpeg", kind=MediaKind.PHOTO),
        as_file=True,
        thumb=None,
        progress=lambda done, total: None,
        span=spans[0],
    )
    store = main.app.state.runtime.store
    await store.update(
        task["id"],
        sent={
            "file_id": file_id,
            "chat_id": task["chat_id"],
            "message": asdict(sent),
            "parts": [asdict(sent)],
            "spans": spans,
        },
    )
    await redeliver(worker, task["id"])
    done = (await client.get(f"/tasks/{task['id']}")).json()
    assert done["status"] == "done" and done["file"]["uuid"] == str(file_id)
    messages = telegram.messages()
    assert [message["name"] for message in messages] == [
        f"crash.jpg.part{index}" for index in range(3)
    ]
    rows = await parts_of(sessions, str(file_id))
    assert rows[0][1] == sent.message_id and len(rows) == 3
    async with sessions() as session:
        row = await session.get(FileDB, file_id)
    assert row.tg_message_id == sent.message_id and row.size == len(data)
    response = await client.get(f"/files/{file_id}/download")
    assert response.content == data
