import asyncio
import shutil
import time
from collections import namedtuple

from src.config import settings
from src.infrastructure.background import MiB
from tests.api.conftest import path, start_upload, upload, wait_task
from tests.factories import EXE, make_image

Usage = namedtuple("Usage", "total used free")


async def test_hundred_parallel_uploads_respect_telegram_limit(
    make_client, telegram, storage, monkeypatch
):
    monkeypatch.setattr(settings.telegram, "CONCURRENCY", 2)
    client = await make_client()
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
    assert telegram.max_sending <= 2
    assert len(telegram.messages()) == 100
    listing = (
        await client.get("/folders", params={"path": path(storage, "many")})
    ).json()
    assert len(listing["files"]) == 100


async def test_waiting_tasks_show_queue_position(
    make_client, telegram, storage, monkeypatch
):
    monkeypatch.setattr(settings.telegram, "CONCURRENCY", 1)
    client = await make_client()
    telegram.delay = 0.5
    ids = [
        await start_upload(client, path(storage, f"{index}.jpg"), make_image())
        for index in range(3)
    ]
    positions = set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(positions) < 2:
        for task_id in ids:
            task = (await client.get(f"/tasks/{task_id}")).json()
            if task["queue_position"]:
                assert task["waiting"] == {
                    "reason": "queue:uploading_tg",
                    "seconds": None,
                }
                positions.add(task["queue_position"])
        await asyncio.sleep(0.02)
    assert positions == {1, 2}
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

    async def flaky(target):
        calls.append(1)
        scanner.available = len(calls) > 1
        await original(target)

    scanner.scan = flaky
    assert (await upload(client, path(storage, "a.jpg"), make_image()))[
        "status"
    ] == "done"
    assert len(calls) == 2


async def test_too_many_active_tasks(client, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "MAX_ACTIVE_TASKS", 3)
    for index in range(3):
        response = await client.post(
            "/uploads", json={"path": path(storage, f"{index}.jpg"), "size": 10}
        )
        assert response.status_code == 201
    response = await client.post(
        "/uploads", json={"path": path(storage, "x.jpg"), "size": 10}
    )
    assert (
        response.status_code == 429
        and response.json()["error"]["code"] == "too_many_tasks"
    )


async def test_stale_pending_tasks_expire(client, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "PENDING_TTL", 0)
    monkeypatch.setattr(settings.upload, "MAX_ACTIVE_TASKS", 1)
    old = (
        await client.post("/uploads", json={"path": path(storage, "a.jpg"), "size": 10})
    ).json()
    await asyncio.sleep(0.01)
    assert (
        await client.post("/uploads", json={"path": path(storage, "a.jpg"), "size": 10})
    ).status_code == 201
    assert (await client.get(f"/tasks/{old['id']}")).json()["error"][
        "code"
    ] == "expired"


async def test_disk_full_rejects_upload(make_client, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "MIN_FREE_DISK_MB", 1)
    client = await make_client()
    monkeypatch.setattr(
        shutil, "disk_usage", lambda target: Usage(10**9, 10**9, MiB + 100)
    )
    response = await client.post(
        "/uploads", json={"path": path(storage, "a.jpg"), "size": 200}
    )
    assert (
        response.status_code == 507
        and response.json()["error"]["code"] == "insufficient_storage"
    )
    assert (
        await client.post("/uploads", json={"path": path(storage, "b.jpg"), "size": 50})
    ).status_code == 201


async def test_batch_upload(client, storage):
    data = make_image()
    items = [
        {"path": path(storage, "b/1.jpg"), "size": len(data)},
        {"path": path(storage, "b/2.jpg"), "size": len(data)},
        {"path": path(storage, "b/doc.pdf"), "size": 10},
    ]
    response = await client.post("/uploads/batch", json={"items": items})
    assert response.status_code == 201
    body = response.json()
    assert [item["error"] is None for item in body["items"]] == [True, True, False]
    assert body["items"][2]["error"]["code"] == "unsupported_media_type"
    for item in body["items"][:2]:
        assert item["task"]["batch_id"] == body["batch_id"]
        await client.put(f"/uploads/{item['task']['id']}", content=data)
        await wait_task(client, item["task"]["id"])
    batch = (await client.get("/tasks", params={"batch_id": body["batch_id"]})).json()
    assert batch["summary"]["total"] == 2 and batch["summary"]["by_status"] == {
        "done": 2
    }
    assert batch["summary"]["progress"] == 100.0 and batch["summary"]["finished"] == 2
    assert all(
        task["file"]["path"].startswith(path(storage, "b/")) for task in batch["items"]
    )


async def test_batch_validation(client, storage):
    assert (await client.post("/uploads/batch", json={"items": []})).status_code == 422
    assert (
        await client.post("/uploads/batch", json={"items": [{"path": "x"}]})
    ).status_code == 422
    both = {"path": path(storage, "a.jpg"), "size": 1, "url": "https://x.com/a.jpg"}
    assert (
        await client.post("/uploads/batch", json={"items": [both]})
    ).status_code == 422
    many = [{"path": path(storage, f"{index}.jpg"), "size": 1} for index in range(101)]
    assert (
        await client.post("/uploads/batch", json={"items": many})
    ).status_code == 422


async def test_batch_same_path_twice(client, storage):
    item = {"path": path(storage, "a.jpg"), "size": 10}
    items = (await client.post("/uploads/batch", json={"items": [item, item]})).json()[
        "items"
    ]
    assert (
        items[0]["error"] is None and items[1]["error"]["code"] == "upload_in_progress"
    )


async def test_list_tasks_filters(client, storage):
    await upload(client, path(storage, "ok.jpg"), make_image())
    await upload(client, path(storage, "bad.jpg"), EXE)
    await client.post("/uploads", json={"path": path(storage, "wait.jpg"), "size": 10})
    tasks = (await client.get("/tasks")).json()
    assert tasks["summary"]["by_status"] == {"done": 1, "failed": 1, "pending": 1}
    assert [task["path"].rsplit("/", 1)[1] for task in tasks["items"]] == [
        "wait.jpg",
        "bad.jpg",
        "ok.jpg",
    ]
    failed = (await client.get("/tasks", params={"status": "failed"})).json()["items"]
    assert [task["error"]["code"] for task in failed] == ["unsupported_media_type"]
    assert (
        len(
            (await client.get("/tasks", params={"limit": 1, "offset": 1})).json()[
                "items"
            ]
        )
        == 1
    )
