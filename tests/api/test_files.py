import io
import tarfile
import uuid
import zipfile

import py7zr
import pytest

from src.config import settings
from tests.api.conftest import path, upload
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
    keep = (await upload(client, path(storage, "other/3.jpg"), make_image()))["file"]
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


async def test_folder_archive_has_the_whole_tree(client, storage):
    image = make_image()
    await upload(client, path(storage, "trip/1.jpg"), image)
    await upload(client, path(storage, "trip/day 2/2.pdf"), PDF)
    await upload(client, path(storage, "other/3.jpg"), image)
    response = await client.get(
        "/folders/archive", params={"path": path(storage, "trip")}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "trip.zip" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == ["1.jpg", "day 2/2.pdf"]
        assert archive.read("day 2/2.pdf") == PDF
    assert list(settings.upload.TMP_DIR.iterdir()) == []


async def test_storage_archive_as_tar_xz(client, storage):
    await upload(client, path(storage, "a/1.jpg"), make_image())
    response = await client.get(
        "/folders/archive", params={"path": path(storage), "format": "tar.xz"}
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith(f"{storage['id']}.tar.xz")
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        assert archive.getnames() == ["a/1.jpg"]


async def test_folder_archive_as_7z(client, storage, tmp_path):
    await upload(client, path(storage, "trip/day 2/2.pdf"), PDF)
    response = await client.get(
        "/folders/archive", params={"path": path(storage, "trip"), "format": "7z"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-7z-compressed"
    assert response.headers["content-disposition"].endswith("trip.7z")
    with py7zr.SevenZipFile(io.BytesIO(response.content)) as archive:
        archive.extractall(tmp_path)
    assert (tmp_path / "day 2" / "2.pdf").read_bytes() == PDF
    assert list(settings.upload.TMP_DIR.iterdir()) == []


async def test_folder_archive_errors(client, storage):
    response = await client.get(
        "/folders/archive", params={"path": path(storage, "nope")}
    )
    assert response.status_code == 404
    response = await client.get(
        "/folders/archive", params={"path": path(storage), "format": "rar"}
    )
    assert response.status_code == 422


async def test_folder_archive_skips_files_gone_from_telegram(client, telegram, storage):
    await upload(client, path(storage, "a/1.jpg"), make_image())
    gone = (await upload(client, path(storage, "a/2.pdf"), PDF))["file"]
    for chat in telegram.chats.values():
        chat["messages"].pop(gone["telegram"]["message_id"], None)
    response = await client.get("/folders/archive", params={"path": path(storage, "a")})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["1.jpg"]
