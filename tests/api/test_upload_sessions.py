import asyncio
import hashlib
import os
import shutil
import uuid
from collections import namedtuple
from pathlib import Path

import pytest
from starlette.requests import ClientDisconnect

import main
from src.application.services import TaskSweeper
from src.application.services.temp import append_body
from src.config import settings
from src.infrastructure.cache import TaskStore
from tests.api.conftest import path, upload, wait_task
from tests.factories import make_image

CHUNK = 10_000
Usage = namedtuple("Usage", "total used free")


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch):
    monkeypatch.setattr(settings.upload, "CHUNK_SIZE", CHUNK)


async def open_session(client, target: str, size: int, **options) -> dict:
    response = await client.post(
        "/uploads/sessions", params={"path": target, "size": size, **options}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def put(client, session_id: str, offset: int, data: bytes, **headers):
    return await client.put(
        f"/uploads/sessions/{session_id}",
        params={"offset": offset},
        content=data,
        headers=headers,
    )


async def send_all(client, session: dict, data: bytes, start: int = 0):
    response = None
    for offset in range(start, len(data), CHUNK):
        response = await put(
            client, session["session_id"], offset, data[offset : offset + CHUNK]
        )
    return response


def store() -> TaskStore:
    return main.app.state.runtime.store


async def test_chunks_end_in_queued_task(client, storage):
    data = os.urandom(CHUNK * 2 + 1234)
    session = await open_session(client, path(storage, "big.bin"), len(data))
    assert session["offset"] == 0 and session["chunk_size"] == CHUNK
    assert session["size"] == len(data)
    assert session["path"] == path(storage, "big.bin")
    first = await put(client, session["session_id"], 0, data[:CHUNK])
    assert first.status_code == 200 and first.json()["offset"] == CHUNK
    last = await send_all(client, session, data, CHUNK)
    assert last.status_code == 202, last.text
    task = await wait_task(client, last.json()["id"])
    assert task["status"] == "done", task
    assert task["file"]["size"] == len(data)
    assert task["file"]["sha256"] == hashlib.sha256(data).hexdigest()
    body = await client.get(f"/files/{task['file']['uuid']}/download")
    assert body.content == data
    gone = await client.get(f"/uploads/sessions/{session['session_id']}")
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "upload_session_not_found"


async def test_resume_after_broken_chunk(client, storage):
    data = os.urandom(CHUNK * 2)
    session = await open_session(client, path(storage, "a.bin"), len(data))
    await put(client, session["session_id"], 0, data[:CHUNK])
    broken = await put(
        client,
        session["session_id"],
        CHUNK,
        data[CHUNK : CHUNK + 100],
        **{"Content-Length": str(CHUNK)},
    )
    assert broken.status_code == 400
    assert broken.json()["error"]["code"] == "size_mismatch"
    head = await client.head(f"/uploads/sessions/{session['session_id']}")
    assert head.status_code == 200 and head.headers["Upload-Offset"] == str(CHUNK)
    state = (await client.get(f"/uploads/sessions/{session['session_id']}")).json()
    assert (state["offset"], state["size"], state["chunk_size"]) == (
        CHUNK,
        len(data),
        CHUNK,
    )
    file = Path(
        (await store().get_session(session["session_id"]))["task"]["params"]["file"]
    )
    assert file.stat().st_size == CHUNK
    last = await send_all(client, session, data, state["offset"])
    task = await wait_task(client, last.json()["id"])
    assert task["file"]["sha256"] == hashlib.sha256(data).hexdigest()


async def test_disconnect_truncates_to_committed_offset(tmp_path):
    target = tmp_path / "part"
    target.write_bytes(b"a" * 10)

    async def body():
        yield b"b" * 5
        raise ClientDisconnect()

    with pytest.raises(ClientDisconnect):
        await append_body(body(), target, 10, 20)
    assert target.read_bytes() == b"a" * 10


async def test_wrong_offset(client, storage):
    session = await open_session(client, path(storage, "a.bin"), CHUNK * 2)
    response = await put(client, session["session_id"], CHUNK, b"x" * CHUNK)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "offset_mismatch"
    assert response.json()["error"]["offset"] == 0


async def test_chunk_too_large(client, storage):
    session = await open_session(client, path(storage, "a.bin"), CHUNK + 10)
    response = await put(client, session["session_id"], 0, b"x" * (CHUNK + 1))
    assert response.status_code == 413
    assert response.json()["error"] == {
        "code": "chunk_too_large",
        "message": f"chunk is {CHUNK + 1} bytes, at most {CHUNK} fit",
        "limit": CHUNK,
    }
    await put(client, session["session_id"], 0, b"x" * CHUNK)
    response = await put(client, session["session_id"], CHUNK, b"x" * 11)
    assert response.status_code == 413 and response.json()["error"]["limit"] == 10

    async def chunks():
        yield b"x"

    response = await client.put(
        f"/uploads/sessions/{session['session_id']}",
        params={"offset": CHUNK},
        content=chunks(),
    )
    assert response.status_code == 411


async def test_abort_releases_path(client, storage):
    target = path(storage, "a.bin")
    session = await open_session(client, target, CHUNK * 2)
    await put(client, session["session_id"], 0, b"x" * CHUNK)
    file = Path(
        (await store().get_session(session["session_id"]))["task"]["params"]["file"]
    )
    busy = await client.post("/uploads/sessions", params={"path": target, "size": 5})
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "upload_in_progress"
    response = await client.delete(f"/uploads/sessions/{session['session_id']}")
    assert response.status_code == 204
    assert not file.exists()
    assert (
        await client.get(f"/uploads/sessions/{session['session_id']}")
    ).status_code == 404
    assert (await upload(client, target, b"plain"))["status"] == "done"


async def test_expired_session_is_swept(client, storage, redis):
    target = path(storage, "a.bin")
    session = await open_session(client, target, CHUNK * 2)
    sessions = TaskStore(redis)
    saved = await sessions.get_session(session["session_id"])
    file = Path(saved["task"]["params"]["file"])
    assert await TaskSweeper(sessions).clean_tmp() == 0 and file.exists()
    await sessions.save_session({**saved, "expires_at": 0})
    response = await put(client, session["session_id"], 0, b"x" * CHUNK)
    assert response.status_code == 404
    assert await TaskSweeper(sessions).clean_tmp() == 1
    assert not file.exists() and await sessions.sessions() == []
    assert not await sessions.path_locked(target)


async def test_live_session_file_survives_orphan_cleanup(
    client, storage, redis, monkeypatch
):
    session = await open_session(client, path(storage, "a.bin"), CHUNK * 2)
    monkeypatch.setattr(settings.upload, "ORPHAN_AGE", -1)
    assert await TaskSweeper(TaskStore(redis)).clean_tmp() == 0
    response = await put(client, session["session_id"], 0, b"x" * CHUNK)
    assert response.status_code == 200


@pytest.mark.parametrize("role", ["user", "admin"])
async def test_other_user_gets_404(client, storage, make_user, role):
    session = await open_session(client, path(storage, "a.bin"), CHUNK)
    other = await make_user(f"other-{role}", role)
    url = f"/uploads/sessions/{session['session_id']}"
    assert (await other.get(url)).status_code == 404
    assert (await other.head(url)).status_code == 404
    assert (await other.delete(url)).status_code == 404
    response = await other.put(url, params={"offset": 0}, content=b"x" * CHUNK)
    assert response.json()["error"]["code"] == "upload_session_not_found"
    assert (await client.get(url)).json()["offset"] == 0
    assert (await client.get(f"/uploads/sessions/{uuid.uuid4()}")).status_code == 404


async def test_conflicts_at_create(client, telegram, storage):
    target = path(storage, "a.jpg")
    first = (await upload(client, target, make_image()))["file"]
    conflict = await client.post(
        "/uploads/sessions", params={"path": target, "size": 5}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["suggested_name"] == "a-2.jpg"
    skipped = await client.post(
        "/uploads/sessions", params={"path": target, "size": 5, "on_conflict": "skip"}
    )
    assert skipped.status_code == 200, skipped.text
    task = skipped.json()
    assert task["status"] == "done" and task["file"]["uuid"] == first["uuid"]
    assert task["warnings"]
    second = await open_session(client, target, 5, on_conflict="keep_both")
    third = await open_session(client, target, 5, on_conflict="keep_both")
    assert (second["path"], third["path"]) == (
        path(storage, "a-2.jpg"),
        path(storage, "a-3.jpg"),
    )
    assert len(telegram.messages()) == 1


async def test_create_checks_size_limit(client, storage):
    saved = await client.patch(
        "/account/settings", json={"uploads": {"max_file_size": 1000}}
    )
    assert saved.status_code == 200
    response = await client.post(
        "/uploads/sessions", params={"path": path(storage, "a.bin"), "size": 1001}
    )
    assert (
        response.status_code == 413 and response.json()["error"]["code"] == "too_large"
    )
    response = await client.post(
        "/uploads/sessions", params={"path": path(storage, "a.bin"), "size": 0}
    )
    assert response.status_code == 422


async def test_session_takes_disk_slot(client, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "MAX_FILES_ON_DISK", 1)
    session = await open_session(client, path(storage, "a.bin"), CHUNK)
    response = await client.post(
        "/uploads/sessions", params={"path": path(storage, "b.bin"), "size": 5}
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_tasks"
    await client.delete(f"/uploads/sessions/{session['session_id']}")
    await open_session(client, path(storage, "b.bin"), 5)


async def test_concurrent_chunk_is_rejected(client, storage):
    session = await open_session(client, path(storage, "a.bin"), CHUNK * 2)
    async with store().holding(f"upload:{session['session_id']}"):
        response = await put(client, session["session_id"], 0, b"x" * CHUNK)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "upload_session_busy"
        deleted = await client.delete(f"/uploads/sessions/{session['session_id']}")
        assert deleted.status_code == 409


async def test_parallel_puts_write_one_chunk(client, storage):
    data = os.urandom(CHUNK * 2)
    session = await open_session(client, path(storage, "a.bin"), len(data))

    async def slow():
        yield data[:100]
        await asyncio.sleep(0.3)
        yield data[100:CHUNK]

    async def late():
        await asyncio.sleep(0.1)
        return await put(client, session["session_id"], 0, data[:CHUNK])

    first, second = await asyncio.gather(
        put(client, session["session_id"], 0, slow(), **{"Content-Length": str(CHUNK)}),
        late(),
    )
    assert first.status_code == 200, first.text
    assert second.json()["error"]["code"] == "upload_session_busy"
    last = await put(client, session["session_id"], CHUNK, data[CHUNK:])
    done = await wait_task(client, last.json()["id"])
    assert done["file"]["sha256"] == hashlib.sha256(data).hexdigest()


@pytest.fixture
def disk(monkeypatch):
    free = {"value": 50_000}
    monkeypatch.setattr(main.app.state.runtime.disk, "min_free", 0)
    monkeypatch.setattr(
        shutil, "disk_usage", lambda target: Usage(10**12, 0, free["value"])
    )
    return free


async def create(client, target: str, size: int, **options):
    return await client.post(
        "/uploads/sessions", params={"path": target, "size": size, **options}
    )


def insufficient(response) -> bool:
    return (
        response.status_code == 507
        and response.json()["error"]["code"] == "insufficient_storage"
    )


async def test_sessions_reserve_disk_until_abort(client, storage, disk):
    first = await open_session(client, path(storage, "a.bin"), 30_000, encrypt="false")
    target = path(storage, "b.bin")
    assert insufficient(await create(client, target, 30_000, encrypt="false"))
    await put(client, first["session_id"], 0, b"x" * CHUNK)
    disk["value"] -= CHUNK
    assert insufficient(await create(client, target, 30_000, encrypt="false"))
    response = await client.delete(f"/uploads/sessions/{first['session_id']}")
    assert response.status_code == 204
    disk["value"] += CHUNK
    assert (await create(client, target, 30_000, encrypt="false")).status_code == 201


async def test_finished_session_frees_reservation(client, storage, disk):
    data = os.urandom(30_000)
    session = await open_session(
        client, path(storage, "a.bin"), len(data), encrypt="false"
    )
    target = path(storage, "b.bin")
    assert insufficient(await create(client, target, 30_000, encrypt="false"))
    last = await send_all(client, session, data)
    assert last.status_code == 202, last.text
    assert (await create(client, target, 30_000, encrypt="false")).status_code == 201
    disk["value"] = 10**12
    assert (await wait_task(client, last.json()["id"]))["status"] == "done"


async def test_expired_session_frees_reservation(client, storage, disk):
    session = await open_session(
        client, path(storage, "a.bin"), 30_000, encrypt="false"
    )
    target = path(storage, "b.bin")
    assert insufficient(await create(client, target, 30_000, encrypt="false"))
    saved = await store().get_session(session["session_id"])
    await store().save_session({**saved, "expires_at": 0})
    assert (await create(client, target, 30_000, encrypt="false")).status_code == 201


async def test_device_upload_sees_encrypted_session(client, storage, disk):
    await open_session(client, path(storage, "a.bin"), 20_000)
    response = await client.post(
        "/uploads",
        params={"path": path(storage, "b.bin"), "encrypt": "false"},
        content=b"x" * 15_000,
    )
    assert insufficient(response)
    response = await client.post(
        "/uploads",
        params={"path": path(storage, "c.bin"), "encrypt": "false"},
        content=b"x" * 5_000,
    )
    assert response.status_code == 202
    disk["value"] = 10**12
    assert (await wait_task(client, response.json()["id"]))["status"] == "done"
