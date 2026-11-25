import io
import tarfile
import zipfile

import main
from src.config import settings
from src.infrastructure.security import HEADER
from tests.api.conftest import path, upload, wait_task
from tests.factories import PDF, make_image


def code(response) -> str:
    return response.json()["error"]["code"]


async def stored(client, storage, rest: str, data: bytes = PDF, **options) -> dict:
    task = await upload(client, path(storage, rest), data, **options)
    assert task["status"] == "done", task
    return task["file"]


async def select(client, **body):
    return await client.post("/folders/archive/selection", json=body)


async def build(client, **body) -> tuple[dict, object]:
    response = await select(client, **body)
    assert response.status_code == 202, response.text
    task = await wait_task(client, response.json()["id"])
    return task, await client.get(f"/tasks/{task['id']}/archive")


def names(response) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return sorted(archive.namelist())


async def test_files_and_folders_keep_structure(client, storage):
    one = await stored(client, storage, "trip/1.pdf")
    await stored(client, storage, "trip/skip.pdf")
    await stored(client, storage, "trip/day 1/a.pdf")
    await stored(client, storage, "trip/day 1/deep/b.pdf")
    await stored(client, storage, "trip/day 2/c.pdf")
    task, response = await build(
        client,
        files=[one["uuid"]],
        folders=[path(storage, "trip/day 1")],
        priority="high",
    )
    assert task["status"] == "done" and task["stage"] == "done"
    assert task["path"] == path(storage, "trip") and task["priority"] == "high"
    assert task["archive"]["name"] == "trip-3-files.zip"
    assert task["archive"]["files"] == 3
    assert names(response) == ["1.pdf", "day 1/a.pdf", "day 1/deep/b.pdf"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("day 1/deep/b.pdf") == PDF


async def test_duplicates_are_archived_once(client, storage):
    inner = await stored(client, storage, "box/in/a.pdf")
    await stored(client, storage, "box/in/sub/b.pdf")
    task, response = await build(
        client,
        files=[inner["uuid"], inner["uuid"]],
        folders=[
            path(storage, "box/in"),
            path(storage, "box/in/sub"),
            path(storage, "box/in"),
        ],
    )
    assert task["archive"]["files"] == 2
    assert names(response) == ["in/a.pdf", "in/sub/b.pdf"]


async def test_only_files_use_their_folder_as_base(client, storage):
    first = await stored(client, storage, "docs/x.pdf")
    second = await stored(client, storage, "docs/y.pdf")
    task, response = await build(
        client, files=[first["uuid"], second["uuid"]], name="Мои документы"
    )
    assert task["archive"]["name"] == "Мои документы.zip"
    assert names(response) == ["x.pdf", "y.pdf"]


async def test_storage_root_selected(client, storage):
    await stored(client, storage, "a/1.pdf")
    await stored(client, storage, "2.pdf")
    task, response = await build(client, folders=[path(storage)], format="tar.gz")
    assert task["archive"]["name"] == f"{storage['id']}-2-files.tar.gz"
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        assert sorted(archive.getnames()) == ["2.pdf", "a/1.pdf"]
        assert archive.extractfile("a/1.pdf").read() == PDF


async def test_selection_as_tar_xz_and_7z(client, storage):
    file = await stored(client, storage, "p/photo.jpg", make_image())
    _, response = await build(client, files=[file["uuid"]], format="tar.xz")
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        assert archive.getnames() == ["photo.jpg"]
    task, response = await build(client, files=[file["uuid"]], format="7z")
    assert task["status"] == "done" and task["archive"]["format"] == "7z"
    assert response.content.startswith(b"7z")


async def test_empty_selection(client):
    response = await select(client)
    assert response.status_code == 422 and code(response) == "empty_selection"
    response = await select(client, files=[], folders=[])
    assert code(response) == "empty_selection"


async def test_too_many_items(client, storage, monkeypatch):
    monkeypatch.setattr(settings.upload, "ARCHIVE_MAX_ITEMS", 2)
    response = await select(
        client,
        folders=[path(storage, "a"), path(storage, "b"), path(storage, "c")],
    )
    assert response.status_code == 422 and code(response) == "too_many_items"
    assert response.json()["error"]["limit"] == 2


async def test_missing_items(client, storage):
    response = await select(client, files=["01900000-0000-7000-8000-000000000000"])
    assert response.status_code == 404 and code(response) == "file_not_found"
    response = await select(client, folders=[path(storage, "nope")])
    assert response.status_code == 404 and code(response) == "folder_not_found"
    response = await select(
        client, folders=["tcloud/01900000-0000-7000-8000-000000000000/x"]
    )
    assert response.status_code == 404 and code(response) == "storage_not_found"


async def test_bad_request_fields(client, storage):
    response = await select(client, folders=["not/a/path"])
    assert response.status_code == 422 and code(response) == "invalid_path"
    response = await select(client, folders=[path(storage)], format="rar")
    assert code(response) == "validation_error"
    response = await select(client, folders=[path(storage)], name="a/b")
    assert code(response) == "validation_error"
    response = await select(client, files=["nope"])
    assert code(response) == "validation_error"


async def test_items_from_two_storages_rejected(client, storage):
    other = (await client.post("/storages", json={"name": "Другое"})).json()
    mine = await stored(client, storage, "a.pdf")
    response = await select(client, files=[mine["uuid"]], folders=[path(other)])
    assert response.status_code == 422 and code(response) == "storage_mismatch"
    theirs = await stored(client, other, "b.pdf")
    response = await select(client, files=[mine["uuid"], theirs["uuid"]])
    assert code(response) == "storage_mismatch"


async def test_access_is_checked(client, storage, make_user):
    shared = await stored(client, storage, "shared/a.pdf")
    await stored(client, storage, "shared/deep/b.pdf")
    secret = await stored(client, storage, "secret/c.pdf")
    await stored(client, storage, "top.pdf")
    reader = await make_user("reader", "user", {"path": path(storage, "shared")})
    response = await select(reader, files=[shared["uuid"], secret["uuid"]])
    assert response.status_code == 403 and code(response) == "forbidden"
    response = await select(reader, folders=[path(storage, "secret")])
    assert response.status_code == 403
    task, archive = await build(reader, folders=[path(storage)])
    assert names(archive) == ["shared/a.pdf", "shared/deep/b.pdf"]
    assert (await client.get(f"/tasks/{task['id']}/archive")).status_code == 404
    flat = await make_user(
        "flat", "user", {"path": path(storage, "shared"), "recursive": False}
    )
    _, archive = await build(flat, folders=[path(storage, "shared")])
    assert names(archive) == ["shared/a.pdf"]


async def test_needs_upload_session(client, storage, upload_session):
    file = await stored(client, storage, "a.pdf")
    upload_session.unlink()
    response = await select(client, files=[file["uuid"]])
    assert response.status_code == 503 and code(response) == "telegram_unauthorized"


async def test_disk_is_checked(client, storage, monkeypatch):
    file = await stored(client, storage, "a.pdf")
    monkeypatch.setattr(main.app.state.runtime.disk, "min_free", 10**18)
    response = await select(client, files=[file["uuid"]])
    assert response.status_code == 507 and code(response) == "insufficient_storage"


async def test_encrypted_files_are_decrypted(client, telegram, storage):
    text = b"secret text " * 10000
    secret = await stored(client, storage, "box/s.txt", text, encrypt="true")
    broken = await stored(client, storage, "box/b.txt", text, encrypt="true")
    message = telegram.chats[broken["telegram"]["chat_id"]]["messages"][
        broken["telegram"]["message_id"]
    ]
    data = bytearray(message["data"])
    data[HEADER + 10] ^= 1
    message["data"] = bytes(data)
    task, response = await build(
        client, files=[secret["uuid"], broken["uuid"]], folders=[]
    )
    assert task["status"] == "done", task
    assert names(response) == ["s.txt"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("s.txt") == text
    assert any("b.txt" in warning for warning in task["warnings"])


async def test_files_inside_selected_folders_are_limited(client, storage, monkeypatch):
    for index in range(3):
        await stored(client, storage, f"a/{index}.pdf")
    monkeypatch.setattr(settings.upload, "ARCHIVE_MAX_FILES", 2)
    response = await select(client, folders=[path(storage, "a")])
    assert response.status_code == 422 and code(response) == "too_many_items"
    assert response.json()["error"]["limit"] == 2
    response = await client.post(
        "/folders/archive", params={"path": path(storage, "a")}
    )
    assert code(response) == "too_many_items"
    monkeypatch.setattr(settings.upload, "ARCHIVE_MAX_FILES", 0)
    monkeypatch.setattr(settings.upload, "ARCHIVE_MAX_SIZE_MB", 1)
    big = await stored(
        client, storage, "b/big.bin", b"x" * (1024 * 1024 + 1), encrypt="false"
    )
    response = await select(client, files=[big["uuid"]])
    assert response.status_code == 413 and code(response) == "too_large"
    assert response.json()["error"]["limit"] == 1024 * 1024
    response = await client.post(
        "/folders/archive", params={"path": path(storage, "b")}
    )
    assert code(response) == "too_large"
    task, _ = await build(client, folders=[path(storage, "a")])
    assert task["status"] == "done"


async def test_one_running_archive_per_user(client, storage, make_user, monkeypatch):
    await stored(client, storage, "a/x.pdf")
    store = main.app.state.runtime.store
    first = await select(client, folders=[path(storage, "a")])
    assert first.status_code == 202
    task_id = first.json()["id"]
    await store.update(task_id, status="running")
    await store.activate(task_id)
    second = await client.post("/folders/archive", params={"path": path(storage, "a")})
    assert second.status_code == 409 and code(second) == "archive_in_progress"
    other = await make_user("other-archiver", "admin")
    third = await other.post("/folders/archive", params={"path": path(storage, "a")})
    assert third.status_code == 202
    await wait_task(other, third.json()["id"])
    await store.update(task_id, status="done")
    again = await client.post("/folders/archive", params={"path": path(storage, "a")})
    assert again.status_code == 202
    await wait_task(client, again.json()["id"])


async def test_legacy_archive_without_top_level_source_still_blocks(client, storage):
    await stored(client, storage, "a/x.pdf")
    store = main.app.state.runtime.store
    first = await select(client, folders=[path(storage, "a")])
    task_id = first.json()["id"]
    await wait_task(client, task_id)
    await store.update(task_id, status="running")
    await store._redis.hdel(store._key("task", task_id), "source")
    second = await client.post("/folders/archive", params={"path": path(storage, "a")})
    assert second.status_code == 409 and code(second) == "archive_in_progress"
    await store.update(task_id, status="done")
    upload_task = await upload(client, path(storage, "b.pdf"), PDF)
    await store.update(upload_task["id"], status="running")
    again = await client.post("/folders/archive", params={"path": path(storage, "a")})
    assert again.status_code == 202
    await wait_task(client, again.json()["id"])
