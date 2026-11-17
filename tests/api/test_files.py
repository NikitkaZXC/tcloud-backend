import io
import tarfile
import uuid
import zipfile
from pathlib import Path

import py7zr
import pytest

from src.application.services import TaskSweeper
from src.config import settings
from src.infrastructure.cache import TaskStore
from tests.api.conftest import build_archive, path, upload, wait_task
from tests.factories import PDF, make_image


@pytest.fixture
async def photo(client, storage):
    return (await upload(client, path(storage, "a/photo.jpg"), make_image()))["file"]


async def test_get_file(client, photo):
    response = await client.get(f"/files/{photo['uuid']}")
    assert response.status_code == 200 and response.json() == photo


async def test_get_file_by_path(client, storage, photo):
    assert (await client.get("/files/by-path", params={"path": photo["path"]})).json()[
        "uuid"
    ] == photo["uuid"]
    response = await client.get(
        "/files/by-path", params={"path": path(storage, "a/nope.jpg")}
    )
    assert (
        response.status_code == 404
        and response.json()["error"]["code"] == "file_not_found"
    )


async def test_file_not_found(client):
    assert (await client.get(f"/files/{uuid.uuid4()}")).status_code == 404


async def test_download_full(client, photo):
    response = await client.get(f"/files/{photo['uuid']}/download")
    assert response.status_code == 200 and response.content == make_image()
    assert (
        response.headers["content-type"] == "image/jpeg"
        and response.headers["accept-ranges"] == "bytes"
    )
    assert "photo.jpg" in response.headers["content-disposition"]


async def test_download_document_as_attachment(client, storage):
    file = (await upload(client, path(storage, "<b>.html"), b"<script>x</script>"))[
        "file"
    ]
    assert file["kind"] == "document"
    response = await client.get(f"/files/{file['uuid']}/download")
    assert response.content == b"<script>x</script>"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"


async def test_download_pdf_range(client, storage):
    file = (await upload(client, path(storage, "a.pdf"), PDF))["file"]
    response = await client.get(
        f"/files/{file['uuid']}/download", headers={"Range": "bytes=0-7"}
    )
    assert response.status_code == 206 and response.content == PDF[:8]


async def test_download_range(client, photo):
    data = make_image()
    response = await client.get(
        f"/files/{photo['uuid']}/download", headers={"Range": "bytes=10-29"}
    )
    assert response.status_code == 206 and response.content == data[10:30]
    assert response.headers["content-range"] == f"bytes 10-29/{len(data)}"
    tail = await client.get(
        f"/files/{photo['uuid']}/download", headers={"Range": "bytes=-5"}
    )
    assert tail.content == data[-5:]
    outside = await client.get(
        f"/files/{photo['uuid']}/download", headers={"Range": f"bytes={len(data)}-"}
    )
    assert outside.status_code == 416


async def test_compressed_photo_size_matches_the_downloaded_bytes(
    client, telegram, storage
):
    data = make_image()
    photo = (await upload(client, path(storage, "images.jpeg"), data, send_as="media"))[
        "file"
    ]
    message = telegram.chats[photo["telegram"]["chat_id"]]["messages"][
        photo["telegram"]["message_id"]
    ]
    last = message["sizes"][-1]
    assert last[1] is not None and len(last[1]) < len(data)
    assert photo["sent_as"] == "media" and photo["size"] == len(data)
    response = await client.get(f"/files/{photo['uuid']}/download")
    assert response.status_code == 200 and response.content == data
    assert response.headers["content-length"] == str(len(data))
    tail = await client.get(
        f"/files/{photo['uuid']}/download", headers={"Range": "bytes=-100"}
    )
    assert tail.status_code == 206 and tail.content == data[-100:]
    assert tail.headers["content-range"] == (
        f"bytes {len(data) - 100}-{len(data) - 1}/{len(data)}"
    )
    task, archive = await build_archive(client, path(storage))
    assert task["status"] == "done" and task["warnings"] == []
    with zipfile.ZipFile(io.BytesIO(archive.content)) as opened:
        assert opened.read("images.jpeg") == data


async def test_delete_file(client, telegram, photo):
    assert (await client.delete(f"/files/{photo['uuid']}")).status_code == 204
    assert telegram.messages() == []
    assert (await client.get(f"/files/{photo['uuid']}")).status_code == 404
    assert (await client.delete(f"/files/{photo['uuid']}")).status_code == 404


async def test_create_and_list_folder(client, storage):
    response = await client.post(
        "/folders", json={"path": path(storage, "Отпуск/2026")}
    )
    assert response.status_code == 201 and response.json()["name"] == "2026"
    root = (await client.get("/folders", params={"path": path(storage)})).json()
    assert root["path"] == path(storage) and [
        item["path"] for item in root["folders"]
    ] == [path(storage, "Отпуск")]
    nested = (
        await client.get("/folders", params={"path": path(storage, "Отпуск/2026")})
    ).json()
    assert nested == {"path": path(storage, "Отпуск/2026"), "folders": [], "files": []}


async def test_create_folder_twice_merges(client, storage):
    first = (await client.post("/folders", json={"path": path(storage, "x")})).json()
    second = (await client.post("/folders", json={"path": path(storage, "x")})).json()
    assert first["id"] == second["id"]


async def test_create_root_folder_rejected(client, storage):
    assert (
        await client.post("/folders", json={"path": path(storage)})
    ).status_code == 422


async def test_list_missing_folder(client, storage):
    response = await client.get("/folders", params={"path": path(storage, "nope")})
    assert (
        response.status_code == 404
        and response.json()["error"]["code"] == "folder_not_found"
    )


async def test_listing_shows_only_direct_children(client, storage):
    await upload(client, path(storage, "a/1.jpg"), make_image())
    await upload(client, path(storage, "a/b/2.jpg"), make_image())
    await upload(client, path(storage, "ab/3.jpg"), make_image())
    listing = (await client.get("/folders", params={"path": path(storage, "a")})).json()
    assert [item["name"] for item in listing["files"]] == ["1.jpg"]
    assert [item["name"] for item in listing["folders"]] == ["b"]


async def test_like_wildcards_in_names_are_literal(client, storage):
    await client.post("/folders", json={"path": path(storage, "a_b")})
    await client.post("/folders", json={"path": path(storage, "a%")})
    await client.post("/folders", json={"path": path(storage, "axb/c")})
    await upload(client, path(storage, "axb/c/1.jpg"), make_image())
    assert (await client.get("/folders", params={"path": path(storage, "a_b")})).json()[
        "folders"
    ] == []
    assert (
        await client.delete("/folders", params={"path": path(storage, "a_b")})
    ).json()["deleted_files"] == 0
    assert (
        await client.get("/folders", params={"path": path(storage, "axb/c")})
    ).status_code == 200


async def test_delete_folder_recursive(client, telegram, storage):
    await upload(client, path(storage, "a/1.jpg"), make_image())
    await upload(client, path(storage, "a/b/2.jpg"), make_image())
    keep = (
        await upload(
            client, path(storage, "other/3.jpg"), make_image(), encrypt="false"
        )
    )["file"]
    response = await client.delete("/folders", params={"path": path(storage, "a")})
    assert response.json() == {"path": path(storage, "a"), "deleted_files": 2}
    assert [message["name"] for message in telegram.messages()] == ["3.jpg"]
    assert (
        await client.get("/folders", params={"path": path(storage, "a/b")})
    ).status_code == 404
    assert (await client.get(f"/files/{keep['uuid']}")).status_code == 200


async def test_delete_root_rejected(client, storage):
    assert (
        await client.delete("/folders", params={"path": path(storage)})
    ).status_code == 422


async def test_delete_missing_folder(client, storage):
    assert (
        await client.delete("/folders", params={"path": path(storage, "nope")})
    ).status_code == 404


async def test_download_refuses_a_replaced_file(client, photo, telegram):
    for chat in telegram.chats.values():
        for message in chat["messages"].values():
            message["data"] = message["data"] + b"tampered"
    response = await client.get(f"/files/{photo['uuid']}/download")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "file_changed"


async def stored_archive(redis, task: dict) -> Path:
    return Path((await TaskStore(redis).get(task["id"]))["archive"]["path"])


async def test_folder_archive_has_the_whole_tree(client, storage, redis):
    image = make_image()
    await upload(client, path(storage, "trip/1.jpg"), image)
    await upload(client, path(storage, "trip/day 2/2.pdf"), PDF)
    await upload(client, path(storage, "other/3.jpg"), image)
    task, response = await build_archive(client, path(storage, "trip"))
    assert task["status"] == "done" and task["stage"] == "done"
    assert task["archive"]["name"] == "trip.zip" and task["archive"]["files"] == 2
    assert task["archive"]["size"] == len(response.content)
    assert task["archive"]["expires_at"] and task["progress"] == 100.0
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].endswith("trip.zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == ["1.jpg", "day 2/2.pdf"]
        assert archive.read("day 2/2.pdf") == PDF
    archive_path = await stored_archive(redis, task)
    assert list(settings.upload.TMP_DIR.iterdir()) == [archive_path]
    assert archive_path.stat().st_mode & 0o777 == 0o600


async def test_archive_download_supports_range(client, storage):
    await upload(client, path(storage, "a/2.pdf"), PDF)
    task, full = await build_archive(client, path(storage, "a"))
    response = await client.get(
        f"/tasks/{task['id']}/archive", headers={"Range": "bytes=10-19"}
    )
    assert response.status_code == 206
    assert response.content == full.content[10:20]


async def test_storage_archive_as_tar_xz(client, storage):
    await upload(client, path(storage, "a/1.jpg"), make_image())
    _, response = await build_archive(client, path(storage), format="tar.xz")
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith(f"{storage['id']}.tar.xz")
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        assert archive.getnames() == ["a/1.jpg"]


async def test_folder_archive_as_7z(client, storage, tmp_path):
    await upload(client, path(storage, "trip/day 2/2.pdf"), PDF)
    _, response = await build_archive(client, path(storage, "trip"), format="7z")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-7z-compressed"
    assert response.headers["content-disposition"].endswith("trip.7z")
    with py7zr.SevenZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(tmp_path)
    assert (tmp_path / "day 2" / "2.pdf").read_bytes() == PDF


async def test_folder_archive_errors(client, storage):
    response = await client.post(
        "/folders/archive", params={"path": path(storage, "nope")}
    )
    assert response.status_code == 404
    response = await client.post(
        "/folders/archive", params={"path": path(storage), "format": "rar"}
    )
    assert response.status_code == 422
    photo = await upload(client, path(storage, "a.jpg"), make_image())
    response = await client.get(f"/tasks/{photo['id']}/archive")
    assert response.status_code == 404
    task, _ = await build_archive(client, path(storage))
    response = await client.post(f"/tasks/{task['id']}/retry")
    assert response.json()["error"]["code"] == "task_not_retryable"


async def test_expired_archive_is_removed(client, storage, redis):
    await upload(client, path(storage, "a/2.pdf"), PDF)
    task, _ = await build_archive(client, path(storage, "a"))
    archive = await stored_archive(redis, task)
    store = TaskStore(redis)
    await store.keep_archive(str(archive), 0)
    assert await TaskSweeper(store).clean_tmp() == 1
    assert not archive.exists() and await store.archives() == {}
    response = await client.get(f"/tasks/{task['id']}/archive")
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "archive_expired"


async def test_kept_archive_survives_orphan_cleanup(
    client, storage, redis, monkeypatch
):
    await upload(client, path(storage, "a/2.pdf"), PDF)
    task, _ = await build_archive(client, path(storage, "a"))
    monkeypatch.setattr(settings.upload, "ORPHAN_AGE", -1)
    assert await TaskSweeper(TaskStore(redis)).clean_tmp() == 0
    assert (await client.get(f"/tasks/{task['id']}/archive")).status_code == 200


async def test_cancelled_archive_leaves_no_files(client, storage, telegram):
    await upload(client, path(storage, "a/2.pdf"), PDF)
    telegram.delay = 0.5
    response = await client.post(
        "/folders/archive", params={"path": path(storage, "a")}
    )
    task_id = response.json()["id"]
    assert (await client.post(f"/tasks/{task_id}/cancel")).status_code == 202
    task = await wait_task(client, task_id)
    assert task["status"] == "cancelled"
    response = await client.get(f"/tasks/{task_id}/archive")
    assert response.json()["error"]["code"] == "archive_not_ready"
    assert list(settings.upload.TMP_DIR.iterdir()) == []


async def test_folder_archive_skips_files_gone_from_telegram(client, telegram, storage):
    await upload(client, path(storage, "a/1.jpg"), make_image())
    gone = (await upload(client, path(storage, "a/2.pdf"), PDF))["file"]
    for chat in telegram.chats.values():
        chat["messages"].pop(gone["telegram"]["message_id"], None)
    task, response = await build_archive(client, path(storage, "a"))
    assert task["warnings"] == ["2.pdf: gone from Telegram, not archived"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["1.jpg"]
