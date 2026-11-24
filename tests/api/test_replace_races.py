import asyncio
import os
import threading
from uuid import UUID

from sqlalchemy import update

import main
from src.infrastructure.database.models import FileDB
from src.infrastructure.database.session import session_factory
from src.infrastructure.integrations import (
    TelegramMessageGoneError,
    TelegramUnavailableError,
)
from tests.api.conftest import path, start_upload, upload, wait_task
from tests.api.test_tasks import redeliver


def pause_after_send(telegram) -> tuple[threading.Event, threading.Event]:
    sent, go = threading.Event(), threading.Event()

    async def hook():
        telegram.sent_hook = None
        sent.set()
        await asyncio.to_thread(go.wait, 10)

    telegram.sent_hook = hook
    return sent, go


async def test_replace_after_target_deleted_keeps_new_file(client, storage, telegram):
    target = path(storage, "r.bin")
    first = await upload(client, target, os.urandom(5000), encrypt="false")
    file_id = first["file"]["uuid"]
    sent, go = pause_after_send(telegram)
    data = os.urandom(7000)
    task_id = await start_upload(
        client, target, data, encrypt="false", on_conflict="replace"
    )
    assert await asyncio.to_thread(sent.wait, 10)
    assert (await client.delete(f"/files/{file_id}")).status_code == 204
    go.set()
    task = await wait_task(client, task_id)
    assert task["status"] == "done"
    meta = await client.get(f"/files/{file_id}")
    assert meta.status_code == 200 and meta.json()["size"] == len(data)
    by_path = await client.get("/files/by-path", params={"path": target})
    assert by_path.status_code == 200
    assert (await client.get(f"/files/{file_id}/download")).content == data


async def test_gone_message_keeps_row_replaced_meanwhile(client, storage, monkeypatch):
    done = await upload(
        client, path(storage, "g.bin"), os.urandom(3000), encrypt="false"
    )
    file_id = UUID(done["file"]["uuid"])
    media = main.app.state.runtime.media

    async def load(telegram, chat_id, message_id):
        async with session_factory() as session:
            await session.execute(
                update(FileDB).where(FileDB.id == file_id).values(tg_message_id=999999)
            )
            await session.commit()
        raise TelegramMessageGoneError("gone")

    monkeypatch.setattr(media, "load", load)
    assert (await client.get(f"/files/{file_id}/download")).status_code == 404
    monkeypatch.undo()
    assert (await client.get(f"/files/{file_id}")).status_code == 200


async def test_replace_finishes_when_old_messages_cannot_be_deleted(
    client, storage, telegram, worker, monkeypatch
):
    target = path(storage, "s.bin")
    first = await upload(client, target, os.urandom(4000), encrypt="false")
    old = first["file"]["telegram"]["message_id"]
    chat_id = first["file"]["telegram"]["chat_id"]
    delete = telegram.delete

    async def broken(chat, message_ids):
        raise TelegramUnavailableError("network is down")

    monkeypatch.setattr(telegram, "delete", broken)
    task = await upload(
        client, target, os.urandom(5000), encrypt="false", on_conflict="replace"
    )
    assert task["status"] == "done"
    assert old in telegram.chats[chat_id]["messages"]
    store = main.app.state.runtime.store
    assert (await store.get(task["id"]))["stale"] == [old]
    monkeypatch.setattr(telegram, "delete", delete)
    await store.update(task["id"], status="running", committed=False, beat_at=0)
    await redeliver(worker, task["id"])
    assert (await client.get(f"/tasks/{task['id']}")).json()["status"] == "done"
    assert old not in telegram.chats[chat_id]["messages"]
    assert (await client.get(f"/files/{first['file']['uuid']}")).status_code == 200
