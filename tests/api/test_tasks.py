import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import asdict
from uuid import UUID, uuid7

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import main
from src.application.services import UploadPipeline
from src.application.services.temp import temp_path
from src.config import settings
from src.infrastructure.database.models import FileDB
from src.infrastructure.enums import MediaKind, UploadQueue
from src.infrastructure.media import Media
from src.utilities.parsers import split_file
from tests.api.conftest import (
    KEY,
    TELEGRAM_CONCURRENCY,
    Remote,
    path,
    start_upload,
    upload,
    wait_task,
)
from tests.factories import make_image


async def wait_for(client, task_id: str, predicate, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    task = {}
    while time.monotonic() < deadline:
        task = (await client.get(f"/tasks/{task_id}")).json()
        if predicate(task):
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(f"condition not met: {task}")


def in_queue(name: str):
    return lambda task: (task["waiting"] or {}).get("reason") == f"queue:{name}"


async def backlog(client, storage, telegram, count: int) -> list[str]:
    telegram.delay = 0.5
    return [
        await start_upload(client, path(storage, f"q/{index}.jpg"), make_image())
        for index in range(count)
    ]


async def test_cancel_task_waiting_in_queue(client, telegram, storage):
    ids = await backlog(client, storage, telegram, TELEGRAM_CONCURRENCY + 2)
    last = await wait_for(client, ids[-1], in_queue("uploads.telegram"))
    response = await client.post(f"/tasks/{last['id']}/cancel")
    assert response.status_code == 202
    cancelled = response.json()
    assert cancelled["status"] == "cancelled" and cancelled["cancel_requested"]
    assert cancelled["error"]["code"] == "cancelled"
    for task_id in ids[:-1]:
        assert (await wait_task(client, task_id))["status"] == "done"
    assert (await client.get(f"/tasks/{last['id']}")).json()["status"] == "cancelled"
    assert len(telegram.messages()) == len(ids) - 1
    assert list(settings.upload.TMP_DIR.iterdir()) == []
    again = await upload(client, last["path"], make_image())
    assert again["status"] == "done"


async def test_cancel_running_upload_deletes_sent_message(client, telegram, storage):
    telegram.delay = 0.5
    task_id = await start_upload(client, path(storage, "a.jpg"), make_image())
    await wait_for(
        client,
        task_id,
        lambda task: task["stage"] == "uploading_tg" and not task["waiting"],
    )
    response = await client.post(f"/tasks/{task_id}/cancel")
    assert response.json()["status"] == "running"
    assert response.json()["cancel_requested"]
    task = await wait_task(client, task_id)
    assert task["status"] == "cancelled" and task["error"]["code"] == "cancelled"
    assert telegram.messages() == []
    listing = (await client.get("/folders", params={"path": path(storage)})).json()
    assert listing["files"] == []


async def test_cancel_finished_and_unknown(client, storage):
    task = await upload(client, path(storage, "a.jpg"), make_image())
    response = await client.post(f"/tasks/{task['id']}/cancel")
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "task_finished"
    )
    assert (await client.post(f"/tasks/{uuid7()}/cancel")).status_code == 404


async def test_change_priority_of_queued_task(client, telegram, storage):
    ids = await backlog(client, storage, telegram, TELEGRAM_CONCURRENCY + 2)
    last = await wait_for(client, ids[-1], in_queue("uploads.telegram"))
    response = await client.post(
        f"/tasks/{last['id']}/priority", json={"priority": "high"}
    )
    assert response.status_code == 200 and response.json()["priority"] == "high"
    for task_id in ids:
        assert (await wait_task(client, task_id))["status"] == "done"
    assert len(telegram.messages()) == len(ids)


async def test_change_priority_needs_queued_task(client, storage):
    task = await upload(client, path(storage, "a.jpg"), make_image())
    response = await client.post(
        f"/tasks/{task['id']}/priority", json={"priority": "high"}
    )
    assert response.json()["error"]["code"] == "task_not_queued"
    response = await client.post(
        f"/tasks/{task['id']}/priority", json={"priority": "urgent"}
    )
    assert response.status_code == 422
    missing = await client.post(f"/tasks/{uuid7()}/priority", json={"priority": "low"})
    assert missing.status_code == 404


async def test_retry_failed_url_upload(client, telegram, storage, remote):
    body = {"url": f"{remote}/later.jpg", "path": path(storage, "later.jpg")}
    task_id = (await client.post("/uploads/from-url", json=body)).json()["id"]
    failed = await wait_task(client, task_id)
    assert failed["error"]["code"] == "download_failed"
    Remote.files["/later.jpg"] = make_image()
    response = await client.post(f"/tasks/{task_id}/retry")
    assert response.status_code == 202 and response.json()["status"] == "queued"
    done = await wait_task(client, task_id)
    assert done["status"] == "done" and done["error"] is None
    assert len(telegram.messages()) == 1


async def test_retry_rules(client, telegram, storage):
    task = await upload(client, path(storage, "a.jpg"), make_image())
    response = await client.post(f"/tasks/{task['id']}/retry")
    assert response.json()["error"]["code"] == "task_not_retryable"
    telegram.flood = 10**6
    failed = await upload(client, path(storage, "b.jpg"), make_image())
    assert failed["status"] == "failed"
    response = await client.post(f"/tasks/{failed['id']}/retry")
    assert (
        response.status_code == 409
        and "upload the file again" in response.json()["error"]["message"]
    )
    assert (await client.post(f"/tasks/{uuid7()}/retry")).status_code == 404


@pytest.fixture
def sessions(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def telegram_ready(client, storage, telegram, *, claimed: bool) -> dict:
    target = path(storage, "crash.jpg")
    storage_id, _, name = split_file(target)
    data = make_image()
    file = temp_path(target)
    file.write_bytes(data)
    task_id = uuid7()
    store = main.app.state.runtime.store
    chat_id = next(iter(telegram.chats))
    media = Media(mime="image/jpeg", kind=MediaKind.PHOTO, width=64, height=48)
    await store.create(
        {
            "id": task_id,
            "status": "running" if claimed else "queued",
            "stage": "uploading_tg",
            "priority": "normal",
            "priority_level": 5,
            "path": target,
            "bytes_done": 0,
            "bytes_total": len(data),
            "error": None,
            "warnings": [],
            "file_id": None,
            "generation": 0,
            "claimed": "uploads.telegram" if claimed else "uploads.prepare",
            "waiting": None,
            "params": {
                "source": "device",
                "send_as": "file",
                "on_conflict": "error",
                "callback_url": None,
                "file": str(file),
            },
            "prepared": {
                "send_path": str(file),
                "thumb": None,
                "media": asdict(media),
                "sha256": hashlib.sha256(data).hexdigest(),
                "as_file": True,
                "warnings": [],
            },
        }
    )
    await store.activate(task_id)
    await store.lock_path(target, task_id)
    return {"id": task_id, "path": target, "chat_id": chat_id, "file": file}


async def crashed_after_send(store, telegram, task: dict) -> UUID:
    file_id = uuid7()
    sent = await telegram.send(
        task["chat_id"],
        task["file"],
        name="crash.jpg",
        caption="x",
        media=Media(mime="image/jpeg", kind=MediaKind.PHOTO),
        as_file=True,
        thumb=None,
        progress=lambda done, total: None,
    )
    await store.update(
        task["id"],
        sent={"file_id": file_id, "chat_id": task["chat_id"], "message": asdict(sent)},
    )
    return file_id


async def redeliver(worker, task_id, redelivered: bool = True) -> None:
    await asyncio.to_thread(
        worker.run,
        UploadPipeline(worker).run(UploadQueue.TELEGRAM, task_id, 0, redelivered),
    )


async def file_rows(sessions) -> list[FileDB]:
    async with sessions() as session:
        return list((await session.scalars(select(FileDB))).all())


async def test_redelivery_after_send_does_not_send_again(
    client, telegram, storage, worker, sessions
):
    task = await telegram_ready(client, storage, telegram, claimed=True)
    store = main.app.state.runtime.store
    file_id = await crashed_after_send(store, telegram, task)
    await redeliver(worker, task["id"])
    done = (await client.get(f"/tasks/{task['id']}")).json()
    assert done["status"] == "done" and done["file"]["uuid"] == str(file_id)
    assert len(telegram.messages()) == 1
    rows = await file_rows(sessions)
    assert [(row.id, row.tg_message_id) for row in rows] == [
        (file_id, telegram.next_message - 1)
    ]
    assert not task["file"].exists()


async def test_redelivery_after_commit_only_finishes(
    client, telegram, storage, worker, sessions
):
    task = await telegram_ready(client, storage, telegram, claimed=True)
    store = main.app.state.runtime.store
    file_id = await crashed_after_send(store, telegram, task)
    await redeliver(worker, task["id"])
    await store.update(task["id"], status="running", committed=False)
    await redeliver(worker, task["id"])
    assert (await client.get(f"/tasks/{task['id']}")).json()["status"] == "done"
    assert len(telegram.messages()) == 1
    async with sessions() as session:
        count = await session.scalar(select(func.count()).select_from(FileDB))
    assert count == 1 and (await file_rows(sessions))[0].id == file_id


async def test_duplicate_message_is_skipped(client, telegram, storage, worker):
    task = await telegram_ready(client, storage, telegram, claimed=True)
    await redeliver(worker, task["id"], redelivered=False)
    status = (await client.get(f"/tasks/{task['id']}")).json()["status"]
    assert status == "running" and telegram.messages() == []
    await main.app.state.runtime.store.release(task["id"], task["path"])


async def test_stale_generation_is_skipped(client, telegram, storage, worker):
    task = await telegram_ready(client, storage, telegram, claimed=False)
    store = main.app.state.runtime.store
    await store.update(task["id"], generation=1)
    await redeliver(worker, task["id"], redelivered=False)
    assert (await store.get(task["id"]))["status"] == "queued"
    assert telegram.messages() == []
    await asyncio.to_thread(
        worker.run,
        UploadPipeline(worker).run(UploadQueue.TELEGRAM, task["id"], 1, False),
    )
    assert (await store.get(task["id"]))["status"] == "done"
    assert len(telegram.messages()) == 1


async def test_cancel_in_queue_sends_webhook(client, telegram, storage, remote):
    telegram.delay = 0.5
    ids = [
        await start_upload(client, path(storage, f"q/{index}.jpg"), make_image())
        for index in range(TELEGRAM_CONCURRENCY + 1)
    ]
    hooked = await start_upload(
        client,
        path(storage, "hooked.jpg"),
        make_image(),
        callback_url=f"{remote}/hook",
    )
    await wait_for(client, hooked, in_queue("uploads.telegram"))
    response = await client.post(f"/tasks/{hooked}/cancel")
    assert response.json()["status"] == "cancelled"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        statuses = [json.loads(body)["status"] for _, body in Remote.hooks]
        if "cancelled" in statuses:
            break
        await asyncio.sleep(0.02)
    assert "cancelled" in statuses
    headers, body = Remote.hooks[-1]
    expected = "sha256=" + hmac.new(KEY.encode(), body, hashlib.sha256).hexdigest()
    assert headers["X-Tcloud-Signature"] == expected
    for task_id in ids:
        await wait_task(client, task_id)


async def test_waiting_tasks_show_queue_position(client, telegram, storage):
    ids = await backlog(client, storage, telegram, TELEGRAM_CONCURRENCY + 3)
    in_telegram = in_queue("uploads.telegram")
    await wait_for(
        client,
        ids[-1],
        lambda task: in_telegram(task) and task["queue_position"] is not None,
    )
    waiting = [
        task
        for task in [(await client.get(f"/tasks/{task_id}")).json() for task_id in ids]
        if in_telegram(task)
    ]
    positions = [task["queue_position"] for task in waiting]
    assert positions == list(range(1, len(waiting) + 1))
    last = waiting[-1]
    await wait_for(
        client,
        last["id"],
        lambda task: (task["queue_position"] or 0) < last["queue_position"],
    )
    for task_id in ids:
        done = await wait_task(client, task_id)
        assert done["status"] == "done" and done["queue_position"] is None
