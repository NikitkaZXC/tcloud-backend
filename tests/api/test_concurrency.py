import asyncio
import shutil
import time
import uuid
from collections import namedtuple

import main
from src.config import settings
from src.infrastructure.background import MiB
from tests.api.conftest import (
    TELEGRAM_CONCURRENCY,
    Remote,
    path,
    start_upload,
    upload,
    wait_task,
)
from tests.factories import make_image

Usage = namedtuple("Usage", "total used free")


async def test_hundred_parallel_uploads_respect_telegram_limit(
    client, telegram, storage
):
    telegram.delay = 0.02
    data = make_image()
    ids = await asyncio.gather(
        *(
            start_upload(client, path(storage, f"many/{index}.jpg"), data)
            for index in range(100)
        )
    )
    results = await asyncio.gather(
        *(wait_task(client, task_id, timeout=60) for task_id in ids)
    )
    assert all(result["status"] == "done" for result in results), [
        r["error"] for r in results if r["error"]
    ][:3]
    assert telegram.max_sending <= TELEGRAM_CONCURRENCY
    assert len(telegram.messages()) == 100
    listing = (
        await client.get("/folders", params={"path": path(storage, "many")})
    ).json()
    assert len(listing["files"]) == 100


async def test_waiting_tasks_show_telegram_queue(client, telegram, storage):
    telegram.delay = 0.5
    ids = [
        await start_upload(client, path(storage, f"{index}.jpg"), make_image())
        for index in range(TELEGRAM_CONCURRENCY + 2)
    ]
    reasons = set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "queue:uploads.telegram" not in reasons:
        for task_id in ids:
            task = (await client.get(f"/tasks/{task_id}")).json()
            if task["waiting"]:
                reasons.add(task["waiting"]["reason"])
        await asyncio.sleep(0.02)
    assert "queue:uploads.telegram" in reasons
    for task_id in ids:
        assert (await wait_task(client, task_id))["status"] == "done"


async def test_flood_wait_pauses_and_resumes(client, telegram, storage):
    telegram.flood_once = 1
    task_id = await start_upload(client, path(storage, "a.jpg"), make_image())
    seen_wait = False
    task = {}
    for _ in range(150):
        task = (await client.get(f"/tasks/{task_id}")).json()
        if task["waiting"] and task["waiting"]["reason"] == "flood_wait":
            seen_wait = True
        if task["status"] == "done":
            break
        await asyncio.sleep(0.02)
    assert seen_wait and task["status"] == "done"


async def test_long_flood_wait_fails(client, telegram, storage, monkeypatch):
    monkeypatch.setattr(settings.telegram, "FLOOD_MAX_WAIT", 100)
    telegram.flood = 300
    task = await upload(client, path(storage, "a.jpg"), make_image())
    assert task["error"] == {
        "code": "flood_wait",
        "message": "Telegram asks to wait 300 s",
        "retry_after": 300,
    }


async def test_network_error_retried(client, telegram, storage):
    telegram.fail_times = 2
    assert (await upload(client, path(storage, "a.jpg"), make_image()))[
        "status"
    ] == "done"


async def test_network_error_gives_up(client, telegram, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "RETRIES", 1)
    telegram.fail_times = 5
    task = await upload(client, path(storage, "a.jpg"), make_image())
    assert (
        task["status"] == "failed" and task["error"]["code"] == "telegram_unavailable"
    )


async def test_scanner_restart_retried(client, scanner, storage):
    calls = []
    original = scanner.scan

    async def flaky(target, progress=None):
        calls.append(1)
        scanner.available = len(calls) > 1
        await original(target, progress)

    scanner.scan = flaky
    assert (await upload(client, path(storage, "a.jpg"), make_image()))[
        "status"
    ] == "done"
    assert len(calls) == 2


async def post_file(client, target: str, size: int | None = None):
    data = make_image()
    headers = {"Content-Length": str(size)} if size else {}
    return await client.post(
        "/uploads", params={"path": target}, content=data, headers=headers
    )


async def test_too_many_active_tasks(client, telegram, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "MAX_ACTIVE_TASKS", 3)
    telegram.delay = 0.5
    for index in range(3):
        response = await post_file(client, path(storage, f"{index}.jpg"))
        assert response.status_code == 202
    response = await post_file(client, path(storage, "x.jpg"))
    assert (
        response.status_code == 429
        and response.json()["error"]["code"] == "too_many_tasks"
    )


async def test_disk_full_rejects_upload(client, storage, monkeypatch):
    monkeypatch.setattr(main.app.state.runtime.disk, "min_free", MiB)
    monkeypatch.setattr(
        shutil, "disk_usage", lambda target: Usage(10**9, 10**9, MiB + 10_000)
    )
    response = await post_file(client, path(storage, "a.jpg"), 20_000)
    assert (
        response.status_code == 507
        and response.json()["error"]["code"] == "insufficient_storage"
    )
    response = await post_file(client, path(storage, "b.jpg"))
    assert response.status_code == 202


async def test_files_on_disk_limit_rejects_device_upload(client, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "MAX_FILES_ON_DISK", 1)
    store = main.app.state.runtime.store
    holder = uuid.uuid4()
    assert await store.take_disk_slot(holder)
    response = await post_file(client, path(storage, "a.jpg"))
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_tasks"
    await store.release(holder, "x")
    response = await post_file(client, path(storage, "a.jpg"))
    assert response.status_code == 202
    assert (await wait_task(client, response.json()["id"]))["status"] == "done"


async def test_url_upload_waits_for_a_disk_slot(
    client, telegram, storage, remote, monkeypatch
):
    monkeypatch.setattr(settings.upload, "MAX_FILES_ON_DISK", 1)
    store = main.app.state.runtime.store
    holder = uuid.uuid4()
    assert await store.take_disk_slot(holder)
    Remote.files["/a.jpg"] = make_image()
    response = await client.post(
        "/uploads/from-url",
        json={"url": f"{remote}/a.jpg", "path": path(storage, "a.jpg")},
    )
    task_id = response.json()["id"]
    for _ in range(100):
        task = (await client.get(f"/tasks/{task_id}")).json()
        if (task["waiting"] or {}).get("reason") == "disk_slots":
            break
        await asyncio.sleep(0.05)
    assert task["waiting"]["reason"] == "disk_slots" and task["status"] == "running"
    await store.release(holder, "x")
    task = await wait_task(client, task_id)
    assert task["status"] == "done"
    probe = uuid.uuid4()
    assert await store.take_disk_slot(probe)
    await store.release(probe, "x")


async def test_zero_limits_mean_unlimited(client, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "MAX_FILES_ON_DISK", 0)
    monkeypatch.setattr(settings.upload, "MAX_ACTIVE_TASKS", 0)
    store = main.app.state.runtime.store
    holders = [uuid.uuid4() for _ in range(3)]
    for holder in holders:
        assert await store.take_disk_slot(holder)
    response = await post_file(client, path(storage, "a.jpg"))
    assert response.status_code == 202
    for holder in holders:
        await store.release(holder, "x")
