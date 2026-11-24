import asyncio

import main
from src.config import settings
from tests.api.conftest import path, wait_task

KB = 1024


def store():
    return main.app.state.runtime.store


async def granted(make_user, storage, login):
    return await make_user(
        login, "user", {"path": path(storage, login), "can_upload": True}
    )


async def test_parallel_direct_uploads_share_the_quota(
    client, storage, make_user, monkeypatch
):
    monkeypatch.setattr(settings.upload, "USER_DISK_QUOTA_MB", 1)
    user = await granted(make_user, storage, "qa1")
    gate = asyncio.Event()
    size = 600 * KB

    async def slow():
        yield b"x" * KB
        await gate.wait()
        yield b"x" * (size - KB)

    first = asyncio.create_task(
        user.post(
            "/uploads",
            params={"path": path(storage, "qa1/a.bin"), "encrypt": "false"},
            content=slow(),
            headers={"Content-Length": str(size)},
        )
    )
    for _ in range(200):
        if (await store().user_disk(user.user_id))[1]:
            break
        await asyncio.sleep(0.01)
    second = await user.post(
        "/uploads",
        params={"path": path(storage, "qa1/b.bin"), "encrypt": "false"},
        content=b"x" * size,
    )
    gate.set()
    assert second.status_code == 507
    assert second.json()["error"]["code"] == "temp_quota_exceeded"
    response = await first
    assert response.status_code == 202
    await wait_task(user, response.json()["id"])
    assert await store().user_disk(user.user_id) == (0, 0)


async def test_queued_plain_files_count_until_sent(
    client, storage, make_user, telegram, monkeypatch
):
    monkeypatch.setattr(settings.upload, "USER_DISK_QUOTA_MB", 1)
    telegram.delay = 1
    user = await granted(make_user, storage, "qa2")
    size = 600 * KB
    first = await user.post(
        "/uploads",
        params={"path": path(storage, "qa2/a.bin"), "encrypt": "false"},
        content=b"y" * size,
    )
    assert first.status_code == 202
    second = await user.post(
        "/uploads",
        params={"path": path(storage, "qa2/b.bin"), "encrypt": "false"},
        content=b"y" * size,
    )
    assert second.json()["error"]["code"] == "temp_quota_exceeded"
    assert second.json()["error"]["used"] == size
    telegram.delay = 0
    assert (await wait_task(user, first.json()["id"]))["status"] == "done"
    assert await store().user_disk(user.user_id) == (0, 0)
    again = await user.post(
        "/uploads",
        params={"path": path(storage, "qa2/b.bin"), "encrypt": "false"},
        content=b"y" * size,
    )
    assert again.status_code == 202
    await wait_task(user, again.json()["id"])


async def test_finished_session_counts_until_sent(
    client, storage, make_user, telegram, monkeypatch
):
    monkeypatch.setattr(settings.upload, "USER_DISK_QUOTA_MB", 1)
    user = await granted(make_user, storage, "qa3")
    size = 600 * KB
    session = (
        await user.post(
            "/uploads/sessions",
            params={
                "path": path(storage, "qa3/a.bin"),
                "size": size,
                "encrypt": "false",
            },
        )
    ).json()
    telegram.delay = 1
    done = await user.put(
        f"/uploads/sessions/{session['session_id']}",
        params={"offset": 0},
        content=b"z" * size,
    )
    assert done.status_code == 202
    assert await store().user_disk(user.user_id) == (0, size)
    telegram.delay = 0
    await wait_task(user, done.json()["id"])
    assert await store().user_disk(user.user_id) == (0, 0)


async def test_archives_respect_the_quota(client, storage, make_user, monkeypatch):
    user = await make_user(
        "qa4", "user", {"path": path(storage, "qa4"), "can_upload": True}
    )
    for name in ("a", "b"):
        response = await client.post(
            "/uploads",
            params={"path": path(storage, f"qa4/{name}.bin"), "encrypt": "false"},
            content=b"q" * (400 * KB),
        )
        await wait_task(client, response.json()["id"])
    monkeypatch.setattr(settings.upload, "USER_DISK_QUOTA_MB", 1)
    response = await user.post(
        "/folders/archive", params={"path": path(storage, "qa4"), "format": "zip"}
    )
    assert response.status_code == 507
    assert response.json()["error"]["code"] == "temp_quota_exceeded"
    monkeypatch.setattr(settings.upload, "USER_DISK_QUOTA_MB", 2)
    response = await user.post(
        "/folders/archive", params={"path": path(storage, "qa4"), "format": "zip"}
    )
    assert response.status_code == 202
    task = await wait_task(user, response.json()["id"])
    assert task["status"] == "done" and task["source"] == "archive"
    assert task["retryable"] is False
    assert await store().user_disk(user.user_id) == (0, task["archive"]["size"])
