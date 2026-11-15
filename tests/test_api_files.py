import uuid

import pytest

from tcloud.api import parse_range
from tcloud.errors import TCloudError
from tests.conftest import image, upload


def p(storage, rest=""):
    return f"tcloud/{storage['id']}" + (f"/{rest}" if rest else "")


@pytest.fixture
def photo(client, storage):
    return upload(client, p(storage, "a/photo.jpg"), image())["file"]


def test_get_file(client, photo):
    r = client.get(f"/files/{photo['uuid']}")
    assert r.status_code == 200 and r.json() == photo


def test_get_file_by_path(client, storage, photo):
    assert client.get("/files/by-path", params={"path": photo["path"]}).json()["uuid"] == photo["uuid"]
    r = client.get("/files/by-path", params={"path": p(storage, "a/nope.jpg")})
    assert r.status_code == 404 and r.json()["error"]["code"] == "file_not_found"


def test_file_not_found(client):
    assert client.get(f"/files/{uuid.uuid4()}").status_code == 404


def test_download_full(client, photo):
    r = client.get(f"/files/{photo['uuid']}/download")
    assert r.status_code == 200 and r.content == image()
    assert r.headers["content-type"] == "image/jpeg" and r.headers["accept-ranges"] == "bytes"
    assert "photo.jpg" in r.headers["content-disposition"]


def test_download_range(client, photo):
    data = image()
    r = client.get(f"/files/{photo['uuid']}/download", headers={"Range": "bytes=10-29"})
    assert r.status_code == 206 and r.content == data[10:30]
    assert r.headers["content-range"] == f"bytes 10-29/{len(data)}"
    assert client.get(f"/files/{photo['uuid']}/download", headers={"Range": "bytes=-5"}).content == data[-5:]
    assert client.get(f"/files/{photo['uuid']}/download", headers={"Range": f"bytes={len(data)}-"}).status_code == 416


@pytest.mark.parametrize("header,size,expected", [
    ("bytes=0-9", 100, (0, 9)),
    ("bytes=90-", 100, (90, 99)),
    ("bytes=-10", 100, (90, 99)),
    ("bytes=-500", 100, (0, 99)),
    ("bytes=50-500", 100, (50, 99)),
])
def test_parse_range(header, size, expected):
    assert parse_range(header, size) == expected


@pytest.mark.parametrize("header", ["bytes=-", "bytes=5-1", "bytes=100-", "items=0-1", "bytes=0-1,5-6"])
def test_parse_range_bad(header):
    with pytest.raises(TCloudError):
        parse_range(header, 100)


def test_delete_file(client, tg, photo):
    assert client.delete(f"/files/{photo['uuid']}").status_code == 204
    assert tg.messages() == []
    assert client.get(f"/files/{photo['uuid']}").status_code == 404
    assert client.delete(f"/files/{photo['uuid']}").status_code == 404


# ---------- папки ----------

def test_create_and_list_folder(client, storage):
    r = client.post("/folders", json={"path": p(storage, "Отпуск/2026")})
    assert r.status_code == 201 and r.json()["name"] == "2026"
    root = client.get("/folders", params={"path": p(storage)}).json()
    assert root["path"] == p(storage) and [f["path"] for f in root["folders"]] == [p(storage, "Отпуск")]
    assert client.get("/folders", params={"path": p(storage, "Отпуск/2026")}).json() == {
        "path": p(storage, "Отпуск/2026"), "folders": [], "files": [],
    }


def test_create_folder_twice_merges(client, storage):
    a = client.post("/folders", json={"path": p(storage, "x")}).json()
    b = client.post("/folders", json={"path": p(storage, "x")}).json()
    assert a["id"] == b["id"]


def test_create_root_folder_rejected(client, storage):
    assert client.post("/folders", json={"path": p(storage)}).status_code == 422


def test_list_missing_folder(client, storage):
    r = client.get("/folders", params={"path": p(storage, "nope")})
    assert r.status_code == 404 and r.json()["error"]["code"] == "folder_not_found"


def test_folder_listing_shows_only_direct_children(client, storage):
    upload(client, p(storage, "a/1.jpg"), image())
    upload(client, p(storage, "a/b/2.jpg"), image())
    upload(client, p(storage, "ab/3.jpg"), image())
    a = client.get("/folders", params={"path": p(storage, "a")}).json()
    assert [f["name"] for f in a["files"]] == ["1.jpg"]
    assert [f["name"] for f in a["folders"]] == ["b"]


def test_like_wildcards_in_names_are_literal(client, storage):
    client.post("/folders", json={"path": p(storage, "a_b")})
    client.post("/folders", json={"path": p(storage, "a%")})
    client.post("/folders", json={"path": p(storage, "axb/c")})
    upload(client, p(storage, "axb/c/1.jpg"), image())
    assert client.get("/folders", params={"path": p(storage, "a_b")}).json()["folders"] == []
    r = client.delete("/folders", params={"path": p(storage, "a_b")})
    assert r.json()["deleted_files"] == 0
    assert client.get("/folders", params={"path": p(storage, "axb/c")}).status_code == 200


def test_delete_folder_recursive(client, tg, storage):
    upload(client, p(storage, "a/1.jpg"), image())
    upload(client, p(storage, "a/b/2.jpg"), image())
    keep = upload(client, p(storage, "other/3.jpg"), image())["file"]
    r = client.delete("/folders", params={"path": p(storage, "a")})
    assert r.json() == {"path": p(storage, "a"), "deleted_files": 2}
    assert [m["name"] for m in tg.messages()] == ["3.jpg"]
    assert client.get("/folders", params={"path": p(storage, "a/b")}).status_code == 404
    assert client.get(f"/files/{keep['uuid']}").status_code == 200


def test_delete_root_rejected(client, storage):
    assert client.delete("/folders", params={"path": p(storage)}).status_code == 422


def test_delete_missing_folder(client, storage):
    assert client.delete("/folders", params={"path": p(storage, "nope")}).status_code == 404


def test_delete_many_files_in_batches(client, tg, storage):
    calls = []
    orig = tg.delete

    async def spy(cid, ids):
        calls.append(len(ids))
        await orig(cid, ids)

    tg.delete = spy
    for i in range(3):
        upload(client, p(storage, f"bulk/{i}.jpg"), image())
    client.delete("/folders", params={"path": p(storage, "bulk")})
    assert calls == [3] and tg.messages() == []
