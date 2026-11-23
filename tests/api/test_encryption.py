import base64
import hashlib
import io
import json
import os
import random
import string
import zipfile

from pydantic import SecretStr

from src.config import settings
from src.infrastructure.security import HEADER, cipher_size
from src.infrastructure.security.encryption import BLOCK
from tests.api.conftest import Remote, build_archive, path, upload, wait_task
from tests.factories import make_image

TEXT = "".join(random.choices(string.ascii_letters, k=3 * BLOCK + 123)).encode()


def code(response) -> str:
    return response.json()["error"]["code"]


async def test_encrypted_upload_round_trip(client, telegram, storage):
    task = await upload(client, path(storage, "docs/secret.txt"), TEXT, encrypt="true")
    assert task["status"] == "done", task
    file = task["file"]
    assert file["encrypted"] is True and file["size"] == len(TEXT)
    assert file["sha256"] == hashlib.sha256(TEXT).hexdigest()
    assert file["name"] == "secret.txt" and file["kind"] == "document"
    message = telegram.messages()[0]
    assert message["as_file"] is True and message["thumb"] is None
    assert message["name"] == f"{file['uuid']}.bin"
    assert len(message["data"]) == cipher_size(len(TEXT))
    assert TEXT[:32] not in message["data"] and b"secret" not in message["data"]
    response = await client.get(f"/files/{file['uuid']}/download")
    assert response.status_code == 200 and response.content == TEXT
    assert response.headers["content-length"] == str(len(TEXT))
    for start, end in ((0, 0), (BLOCK - 3, BLOCK + 3), (2 * BLOCK, len(TEXT) - 1)):
        part = await client.get(
            f"/files/{file['uuid']}/download",
            headers={"Range": f"bytes={start}-{end}"},
        )
        assert part.status_code == 206 and part.content == TEXT[start : end + 1]
        assert part.headers["content-range"] == f"bytes {start}-{end}/{len(TEXT)}"
    assert list(settings.upload.TMP_DIR.iterdir()) == []


async def test_plain_upload_is_not_encrypted(client, telegram, storage):
    file = (await upload(client, path(storage, "a.txt"), TEXT, encrypt="false"))["file"]
    assert file["encrypted"] is False
    assert telegram.messages()[0]["data"] == TEXT


async def test_upload_is_encrypted_by_default(client, telegram, storage):
    file = (await upload(client, path(storage, "a.txt"), TEXT))["file"]
    assert file["encrypted"] is True and file["sent_as"] == "file"
    assert file["size"] == len(TEXT)
    assert len(telegram.messages()[0]["data"]) == cipher_size(len(TEXT))
    assert TEXT[:32] not in telegram.messages()[0]["data"]
    assert telegram.messages()[0]["name"].endswith(".bin")
    response = await client.get(f"/files/{file['uuid']}/download")
    assert response.content == TEXT


async def test_media_upload_is_plain_by_default(client, telegram, storage):
    file = (
        await upload(client, path(storage, "a.jpg"), make_image(), send_as="media")
    )["file"]
    assert file["encrypted"] is False and file["sent_as"] == "media"
    assert telegram.messages()[0]["data"] == make_image()


async def test_url_uploads_are_encrypted_by_default(client, telegram, storage, remote):
    Remote.files["/a.txt"] = TEXT
    Remote.files["/b.jpg"] = make_image()
    single = await client.post(
        "/uploads/from-url",
        json={"path": path(storage, "a.txt"), "url": f"{remote}/a.txt"},
    )
    assert single.status_code == 202, single.text
    batch = await client.post(
        "/uploads/batch",
        json={
            "items": [
                {"path": path(storage, "b.jpg"), "url": f"{remote}/b.jpg"},
                {
                    "path": path(storage, "m.jpg"),
                    "url": f"{remote}/b.jpg",
                    "send_as": "media",
                },
                {
                    "path": path(storage, "p.txt"),
                    "url": f"{remote}/a.txt",
                    "encrypt": False,
                },
            ]
        },
    )
    assert batch.status_code == 202, batch.text
    tasks = [await wait_task(client, single.json()["id"])] + [
        await wait_task(client, item["task"]["id"]) for item in batch.json()["items"]
    ]
    assert [task["status"] for task in tasks] == ["done"] * 4, tasks
    assert [task["file"]["encrypted"] for task in tasks] == [True, True, False, False]


async def test_media_cannot_be_encrypted(client, storage):
    response = await client.post(
        "/uploads",
        params={"path": path(storage, "a.jpg"), "send_as": "media", "encrypt": "true"},
        content=make_image(),
    )
    assert response.status_code == 422 and code(response) == "encrypt_requires_file"


async def test_photo_sent_as_file_is_encrypted(client, telegram, storage):
    file = (await upload(client, path(storage, "p.jpg"), make_image(), encrypt="true"))[
        "file"
    ]
    assert file["encrypted"] and file["kind"] == "photo"
    assert telegram.messages()[0]["data"][HEADER : HEADER + 3] != b"\xff\xd8\xff"
    response = await client.get(f"/files/{file['uuid']}/download")
    assert response.content == make_image()


async def test_encryption_needs_a_server_key(client, storage, monkeypatch):
    monkeypatch.setattr(settings.app, "ENCRYPTION_KEY", None)
    response = await client.post(
        "/uploads",
        params={"path": path(storage, "a.txt"), "encrypt": "true"},
        content=TEXT,
    )
    assert response.status_code == 503 and code(response) == "encryption_unavailable"


async def test_wrong_server_key_refuses_download(client, storage, monkeypatch):
    file = (await upload(client, path(storage, "a.txt"), TEXT, encrypt="true"))["file"]
    other = SecretStr(base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(settings.app, "ENCRYPTION_KEY", other)
    response = await client.get(f"/files/{file['uuid']}/download")
    assert response.status_code == 409 and code(response) == "decryption_failed"


async def test_replace_switches_between_encrypted_and_plain(client, storage):
    target = path(storage, "a.txt")
    first = (await upload(client, target, b"plain text", encrypt="false"))["file"]
    second = (
        await upload(client, target, TEXT, encrypt="true", on_conflict="replace")
    )["file"]
    assert second["uuid"] == first["uuid"] and second["encrypted"]
    assert (await client.get(f"/files/{first['uuid']}/download")).content == TEXT
    third = (
        await upload(
            client, target, b"plain again", encrypt="false", on_conflict="replace"
        )
    )["file"]
    assert third["encrypted"] is False and third["size"] == len(b"plain again")
    response = await client.get(f"/files/{first['uuid']}/download")
    assert response.content == b"plain again"


async def test_archive_decrypts_files_and_skips_damaged(client, telegram, storage):
    await upload(client, path(storage, "box/secret.txt"), TEXT, encrypt="true")
    await upload(client, path(storage, "box/broken.txt"), TEXT, encrypt="true")
    await upload(client, path(storage, "box/plain.txt"), b"open", encrypt="false")
    broken = [m for m in telegram.messages() if m["name"].endswith(".bin")][-1]
    data = bytearray(broken["data"])
    data[HEADER + 10] ^= 1
    broken["data"] = bytes(data)
    task, response = await build_archive(client, path(storage, "box"))
    assert task["status"] == "done", task
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert "plain.txt" in names and len(names) == 2
        survivor = (names - {"plain.txt"}).pop()
        assert archive.read(survivor) == TEXT
    assert any("not archived" in warning for warning in task["warnings"])


async def test_task_forgets_the_preview_after_commit(client, storage, redis):
    task = await upload(client, path(storage, "a.jpg"), make_image("JPEG", (400, 300)))
    stored = await redis.hgetall(f"tcloud:task:{task['id']}")
    assert json.loads(stored["prepared"])["thumbnail"] is None
    assert json.loads(stored["committed"]) is True
    response = await client.get(f"/files/{task['file']['uuid']}/thumb")
    assert response.status_code == 200 and response.content
