import asyncio
import time
import uuid
from collections import defaultdict
from pathlib import Path

import pytest
from starlette.requests import ClientDisconnect

import main
from src.application.services import pipeline
from src.config import settings
from src.infrastructure.cache import TaskStore
from tests.api.conftest import Remote, path, start_upload, upload, wait_task
from tests.api.test_tasks import wait_for
from tests.factories import PDF, make_image

PLAIN = {"encrypt": "false"}


@pytest.fixture
def steps(monkeypatch) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = defaultdict(list)
    original = TaskStore.update

    async def update(self, task_id, **values):
        if "step" in values:
            seen[str(task_id)].append(values["step"] and str(values["step"]))
        await original(self, task_id, **values)

    monkeypatch.setattr(TaskStore, "update", update)
    return seen


def on_step(step: str):
    return lambda task: task["step"] == step and not task["waiting"]


async def cancel_on(client, task_id: str, step: str) -> dict:
    await wait_for(client, task_id, on_step(step))
    started = time.monotonic()
    response = await client.post(f"/tasks/{task_id}/cancel")
    assert response.status_code == 202 and response.json()["cancel_requested"]
    task = await wait_task(client, task_id)
    assert task["status"] == "cancelled" and task["error"]["code"] == "cancelled"
    assert time.monotonic() - started < 5
    assert list(settings.upload.TMP_DIR.iterdir()) == []
    return task


async def test_device_upload_steps(client, storage, steps):
    response = await client.post(
        "/uploads",
        params={"path": path(storage, "a.jpg"), **PLAIN},
        content=make_image(),
    )
    assert response.json()["step"] == "queued_for_prepare"
    task = await wait_task(client, response.json()["id"])
    assert task["status"] == "done" and task["step_progress"] is None
    assert steps[task["id"]] == [
        "analyzing",
        "scanning",
        "queued_for_telegram",
        "sending",
        "finishing",
    ]


async def test_encrypted_photo_steps(client, storage, steps):
    task = await upload(client, path(storage, "a.jpg"), make_image())
    assert task["status"] == "done"
    assert steps[task["id"]] == [
        "analyzing",
        "scanning",
        "encrypting",
        "previewing",
        "queued_for_telegram",
        "sending",
        "finishing",
    ]


async def test_converted_photo_step(client, storage, steps):
    task = await upload(
        client,
        path(storage, "a.webp"),
        make_image("WEBP"),
        send_as="media",
        **PLAIN,
    )
    assert task["status"] == "done"
    assert steps[task["id"]][:3] == ["analyzing", "scanning", "converting"]


async def test_url_upload_steps(client, storage, remote, steps):
    Remote.files["/a.pdf"] = PDF
    response = await client.post(
        "/uploads/from-url",
        json={"url": f"{remote}/a.pdf", "path": path(storage, "a.pdf"), **PLAIN},
    )
    assert response.json()["step"] == "queued_for_download"
    task = await wait_task(client, response.json()["id"])
    assert task["status"] == "done"
    assert steps[task["id"]] == [
        "downloading",
        "queued_for_prepare",
        "analyzing",
        "scanning",
        "queued_for_telegram",
        "sending",
        "finishing",
    ]


async def test_sending_reports_step_progress(client, telegram, storage):
    telegram.parts, telegram.part_delay = 50, 0.05
    task_id = await start_upload(client, path(storage, "a.jpg"), make_image(), **PLAIN)
    task = await wait_for(
        client,
        task_id,
        lambda task: task["step"] == "sending" and (task["step_progress"] or 0) > 0,
    )
    assert 0 < task["step_progress"] <= 1
    assert task["step_progress"] == pytest.approx(task["progress"] / 100, abs=0.001)
    assert (await wait_task(client, task_id))["status"] == "done"


async def test_cancel_while_downloading(client, storage, remote, monkeypatch):
    async def slow(url, target: Path, limit, progress):
        target.write_bytes(b"x")
        for done in range(1000):
            progress(done, 1000)
            await asyncio.sleep(0.01)

    monkeypatch.setattr(pipeline, "download", slow)
    response = await client.post(
        "/uploads/from-url",
        json={"url": f"{remote}/a.pdf", "path": path(storage, "a.pdf")},
    )
    await cancel_on(client, response.json()["id"], "downloading")


async def test_cancel_while_waiting_for_disk_slot(client, storage, remote, monkeypatch):
    monkeypatch.setattr(settings.upload, "MAX_FILES_ON_DISK", 1)
    store = main.app.state.runtime.store
    holder = uuid.uuid4()
    assert await store.take_disk_slot(holder)
    response = await client.post(
        "/uploads/from-url",
        json={"url": f"{remote}/a.pdf", "path": path(storage, "a.pdf")},
    )
    task_id = response.json()["id"]
    await wait_for(
        client,
        task_id,
        lambda task: (task["waiting"] or {}).get("code") == "disk_slots",
    )
    await client.post(f"/tasks/{task_id}/cancel")
    assert (await wait_task(client, task_id, timeout=5))["status"] == "cancelled"
    await store.release(holder, "x")


async def test_cancel_while_analyzing(client, storage, monkeypatch):
    def slow(target: Path, progress) -> str:
        for done in range(1000):
            progress(done)
            time.sleep(0.01)
        return "0" * 64

    monkeypatch.setattr(pipeline, "sha256_of", slow)
    task_id = await start_upload(client, path(storage, "a.jpg"), make_image())
    await cancel_on(client, task_id, "analyzing")


async def test_cancel_while_scanning(client, scanner, storage):
    scanner.parts, scanner.delay = 500, 0.02
    task_id = await start_upload(client, path(storage, "a.jpg"), make_image())
    await cancel_on(client, task_id, "scanning")


async def test_cancel_while_waiting_for_antivirus_retry(
    client, scanner, storage, monkeypatch
):
    scanner.available = False
    monkeypatch.setattr(settings.upload, "RETRY_DELAY", 60)
    task_id = await start_upload(client, path(storage, "a.jpg"), make_image())
    await wait_for(
        client, task_id, lambda task: (task["waiting"] or {}).get("code") == "retry"
    )
    started = time.monotonic()
    await client.post(f"/tasks/{task_id}/cancel")
    assert (await wait_task(client, task_id))["status"] == "cancelled"
    assert time.monotonic() - started < 5
    assert list(settings.upload.TMP_DIR.iterdir()) == []


async def test_cancel_while_encrypting(client, storage, monkeypatch):
    def slow(source: Path, target: Path, progress) -> bytes:
        target.write_bytes(b"partial")
        for done in range(1000):
            progress(done)
            time.sleep(0.01)
        return b""

    monkeypatch.setattr(pipeline, "encrypt_file", slow)
    task_id = await start_upload(client, path(storage, "a.pdf"), PDF)
    await cancel_on(client, task_id, "encrypting")


async def test_cancel_mid_file_while_sending(client, telegram, storage):
    telegram.parts, telegram.part_delay = 500, 0.02
    task_id = await start_upload(client, path(storage, "a.jpg"), make_image())
    await cancel_on(client, task_id, "sending")
    assert telegram.messages() == []


async def test_cancel_during_flood_wait(client, telegram, storage):
    telegram.flood_once = 600
    task_id = await start_upload(client, path(storage, "a.jpg"), make_image())
    await wait_for(
        client,
        task_id,
        lambda task: (task["waiting"] or {}).get("code") == "flood_wait",
    )
    started = time.monotonic()
    await client.post(f"/tasks/{task_id}/cancel")
    assert (await wait_task(client, task_id))["status"] == "cancelled"
    assert time.monotonic() - started < 5
    assert telegram.messages() == []


async def test_cancel_after_message_sent_deletes_it(client, telegram, storage, worker):
    ids: list[str] = []

    async def cancel_now():
        await worker.store.cancel(ids[0])

    telegram.delay, telegram.sent_hook = 0.3, cancel_now
    ids.append(await start_upload(client, path(storage, "a.jpg"), make_image()))
    task = await wait_task(client, ids[0])
    assert task["status"] == "cancelled" and task["step"] == "sending"
    assert telegram.messages() == []
    page = (await client.get("/folders", params={"path": path(storage)})).json()
    assert page["items"] == []


async def test_client_disconnect_mid_body_leaves_nothing(client, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "MAX_FILES_ON_DISK", 1)
    data = make_image()

    async def chunks():
        yield data[:100]
        raise ClientDisconnect()

    target = path(storage, "a.jpg")
    with pytest.raises(ClientDisconnect):
        await client.post(
            "/uploads",
            params={"path": target},
            content=chunks(),
            headers={"Content-Length": str(len(data))},
        )
    assert list(settings.upload.TMP_DIR.iterdir()) == []
    assert (await client.get("/tasks/counts")).json()["total"] == 0
    assert (await upload(client, target, data))["status"] == "done"


async def test_previews_off_skips_previewing(client, storage, steps):
    off = await client.patch("/account/settings", json={"uploads": {"previews": False}})
    assert off.status_code == 200, off.text
    task = await upload(client, path(storage, "a.jpg"), make_image())
    assert task["status"] == "done"
    assert "previewing" not in steps[task["id"]]
    assert "encrypting" in steps[task["id"]]
    thumb = await client.get(f"/files/{task['file']['uuid']}/thumb")
    assert thumb.status_code == 404
