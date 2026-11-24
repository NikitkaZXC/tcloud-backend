import asyncio
import hashlib
import hmac
import json
import uuid

import pytest

from src.config import settings
from src.infrastructure import limits
from src.infrastructure.media import analyze as probe_module
from tests.api.conftest import Remote, listing, path, start_upload, upload, wait_task
from tests.factories import (
    EXE,
    PDF,
    make_audio,
    make_gif_animated,
    make_image,
    make_video,
    make_zip,
)


async def test_upload_photo_as_file(client, telegram, storage):
    data = make_image()
    task = await upload(
        client, path(storage, "отпуск/2026/photo.jpg"), data, encrypt="false"
    )
    assert task["status"] == "done" and task["progress"] == 100.0, task
    file = task["file"]
    assert file["path"] == path(storage, "отпуск/2026/photo.jpg")
    assert (file["size"], file["kind"], file["sent_as"], file["mime"]) == (
        len(data),
        "photo",
        "file",
        "image/jpeg",
    )
    assert (file["width"], file["height"]) == (64, 48)
    assert file["sha256"] == hashlib.sha256(data).hexdigest()
    message = telegram.messages()[0]
    assert (
        message["data"] == data
        and message["as_file"] is True
        and message["name"] == "photo.jpg"
    )
    assert (
        message["caption"]
        == f"#tc{uuid.UUID(file['uuid']).hex}"
        == file["telegram"]["search"]
    )
    assert len(message["caption"]) <= limits.CAPTION_MAX
    assert file["telegram"]["link"].startswith("https://t.me/c/")


async def test_upload_creates_parent_folders(client, storage):
    await upload(client, path(storage, "a/b/c/x.jpg"), make_image())
    root = await listing(client, path(storage))
    assert [item["name"] for item in root["folders"]] == ["a"]
    nested = await listing(client, path(storage, "a/b"))
    assert [item["name"] for item in nested["folders"]] == ["c"]


async def test_photo_as_media(client, telegram, storage):
    task = await upload(
        client, path(storage, "a.png"), make_image("PNG"), send_as="media"
    )
    assert task["file"]["sent_as"] == "media" and task["warnings"] == []
    assert telegram.messages()[0]["as_file"] is False


@pytest.mark.parametrize("name", ["photo.webp", "photo.heic"])
async def test_jpeg_named_as_other_photo_sent_as_photo(client, telegram, storage, name):
    task = await upload(client, path(storage, name), make_image(), send_as="media")
    assert task["status"] == "done"
    message = telegram.messages()[0]
    assert message["as_file"] is False and message["path"].endswith(".jpg")
    assert message["data"] == make_image()


async def test_webp_as_media_converted_to_jpeg(client, telegram, storage):
    task = await upload(
        client, path(storage, "a.webp"), make_image("WEBP"), send_as="media"
    )
    assert task["status"] == "done"
    assert telegram.messages()[0]["data"][:3] == b"\xff\xd8\xff"
    assert task["file"]["sha256"] == hashlib.sha256(make_image("WEBP")).hexdigest()


async def test_big_photo_as_media_falls_back_to_file(
    client, telegram, storage, monkeypatch
):
    monkeypatch.setattr(probe_module, "PHOTO_MAX", 100)
    task = await upload(client, path(storage, "big.jpg"), make_image(), send_as="media")
    assert task["file"]["sent_as"] == "file" and "sent as file" in task["warnings"][0]
    assert telegram.messages()[0]["as_file"] is True


async def test_wide_photo_as_media_falls_back_to_file(client, storage):
    task = await upload(
        client,
        path(storage, "pano.png"),
        make_image("PNG", (2100, 100)),
        send_as="media",
    )
    assert task["file"]["sent_as"] == "file" and task["warnings"]


@pytest.mark.ffmpeg
async def test_video_as_media_has_attributes_and_thumb(client, telegram, storage):
    task = await upload(
        client, path(storage, "clip.mp4"), make_video("mp4", 640, 360), send_as="media"
    )
    file = task["file"]
    assert (file["kind"], file["sent_as"], file["width"], file["height"]) == (
        "video",
        "media",
        640,
        360,
    )
    assert 1.5 < file["duration"] < 2.5
    message = telegram.messages()[0]
    assert message["media"].width == 640 and message["as_file"] is False
    assert (
        message["thumb"][:3] == b"\xff\xd8\xff"
        and len(message["thumb"]) <= limits.THUMB_MAX
    )
    assert message["path"].endswith(".mp4")


@pytest.mark.ffmpeg
async def test_mkv_as_media_warns(client, storage):
    task = await upload(
        client, path(storage, "clip.mkv"), make_video("mkv"), send_as="media"
    )
    assert task["status"] == "done" and "x-matroska" in task["warnings"][0]


@pytest.mark.ffmpeg
async def test_audio(client, storage):
    task = await upload(
        client, path(storage, "song.mp3"), make_audio("mp3"), send_as="media"
    )
    assert task["file"]["kind"] == "audio" and task["file"]["duration"] > 1.5


async def test_animation(client, storage):
    task = await upload(
        client, path(storage, "fun.gif"), make_gif_animated(), send_as="media"
    )
    assert task["file"]["kind"] == "animation"


async def post_file(client, target: str, data: bytes = b"x" * 10, **options):
    headers = options.pop("headers", {})
    return await client.post(
        "/uploads", params={"path": target, **options}, content=data, headers=headers
    )


@pytest.mark.parametrize(
    "name, data",
    [
        ("setup.exe", EXE),
        ("app.dmg", b"koly" + b"\0" * 100),
        ("run.sh", b"#!/bin/sh\necho hi\n"),
        ("noext", EXE),
    ],
)
async def test_any_file_type_accepted(client, telegram, storage, name, data):
    task = await upload(client, path(storage, name), data, encrypt="false")
    assert task["status"] == "done" and task["file"]["kind"] == "document", task
    assert telegram.messages()[0]["data"] == data
    response = await client.get(f"/files/{task['file']['uuid']}/download")
    assert response.content == data
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"


async def test_upload_document(client, telegram, storage):
    task = await upload(client, path(storage, "docs/report.pdf"), PDF, encrypt="false")
    assert task["status"] == "done" and task["warnings"] == [], task
    file = task["file"]
    assert (file["kind"], file["mime"], file["sent_as"], file["size"]) == (
        "document",
        "application/pdf",
        "file",
        len(PDF),
    )
    message = telegram.messages()[0]
    assert message["as_file"] is True and message["name"] == "report.pdf"
    assert message["data"] == PDF and message["thumb"] is None


async def test_document_as_media_is_sent_as_file(client, telegram, storage):
    task = await upload(client, path(storage, "notes.txt"), b"hello", send_as="media")
    assert task["file"]["sent_as"] == "file" and task["file"]["kind"] == "document"
    assert task["warnings"] == ["file is not media, sent as file"]
    assert task["warning_details"] == [
        {
            "code": "not_media_sent_as_file",
            "params": {},
            "message": "file is not media, sent as file",
        }
    ]
    assert telegram.messages()[0]["as_file"] is True


async def test_document_named_as_photo_mismatch(client, telegram, storage):
    task = await upload(client, path(storage, "a.jpg"), PDF)
    assert task["error"]["code"] == "extension_mismatch" and telegram.messages() == []


async def test_archive_with_executable_accepted(client, telegram, storage):
    data = make_zip({"readme.txt": b"x", "bin/setup.exe": b"MZ"})
    task = await upload(client, path(storage, "a.zip"), data)
    assert task["status"] == "done" and task["file"]["kind"] == "document", task
    assert len(telegram.messages()) == 1


async def test_infected_document_rejected(client, telegram, storage):
    task = await upload(client, path(storage, "a.zip"), make_zip({"e.txt": b"EICAR"}))
    assert task["error"]["code"] == "infected" and telegram.messages() == []


async def test_unscannable_rejected(client, telegram, storage):
    task = await upload(client, path(storage, "secret.zip"), b"PK ENCRYPTED")
    assert task["status"] == "failed" and task["error"]["code"] == "unscannable"
    assert telegram.messages() == []


async def test_unscannable_allowed_with_flag(client, telegram, storage):
    task = await upload(
        client, path(storage, "secret.7z"), b"ENCRYPTED", allow_unscannable="true"
    )
    assert task["status"] == "done"
    assert task["warnings"] == [
        "antivirus cannot check this file fully (Heuristics.Encrypted.Zip),"
        " accepted by allow_unscannable"
    ]
    assert task["warning_details"][0]["code"] == "unscannable_accepted"
    assert task["warning_details"][0]["params"] == {
        "reason": "Heuristics.Encrypted.Zip"
    }
    assert len(telegram.messages()) == 1


async def test_allow_unscannable_does_not_pass_viruses(client, telegram, storage):
    task = await upload(
        client, path(storage, "a.zip"), b"ENCRYPTED EICAR", allow_unscannable="true"
    )
    assert task["error"]["code"] == "infected" and telegram.messages() == []


async def test_extension_mismatch(client, storage):
    task = await upload(client, path(storage, "photo.mp4"), make_image())
    assert task["error"]["code"] == "extension_mismatch"


async def test_infected_rejected(client, telegram, storage):
    task = await upload(client, path(storage, "a.jpg"), make_image() + b"EICAR")
    assert task["status"] == "failed" and task["error"]["code"] == "infected"
    assert telegram.messages() == []


async def test_scanner_down_fails_closed(
    client, telegram, scanner, storage, monkeypatch
):
    monkeypatch.setattr(settings.upload, "RETRIES", 0)
    scanner.available = False
    task = await upload(client, path(storage, "a.jpg"), make_image())
    assert task["error"]["code"] == "scanner_unavailable" and telegram.messages() == []


def declared(size: int) -> dict:
    return {"headers": {"Content-Length": str(size)}}


async def test_size_over_limit(client, storage):
    target = path(storage, "a.mp4")
    plain = {"encrypt": "false"}
    response = await post_file(client, target, **plain, **declared(limits.FILE_MAX + 1))
    assert response.json()["error"]["code"] == "size_mismatch"
    saved = await client.patch(
        "/account/settings", json={"uploads": {"max_file_size": 1000}}
    )
    assert saved.status_code == 200, saved.text
    for options in (plain, {}):
        response = await post_file(client, target, **options, **declared(1001))
        assert response.status_code == 413
        assert response.json()["error"] == {
            "code": "too_large",
            "message": "file is 1001 bytes, the server limit is 1000",
            "limit": 1000,
        }
    response = await post_file(client, target, **plain, **declared(1000))
    assert response.json()["error"]["code"] == "size_mismatch"


async def test_body_bigger_than_declared(client, storage):
    data = make_image()
    response = await post_file(
        client, path(storage, "a.jpg"), data, **declared(len(data) - 1)
    )
    assert response.status_code == 413
    response = await post_file(client, path(storage, "a.jpg"), data)
    assert response.status_code == 202


async def test_body_smaller_than_declared(client, storage):
    data = make_image()
    response = await post_file(
        client, path(storage, "a.jpg"), data, **declared(len(data) + 1)
    )
    assert (
        response.status_code == 400
        and response.json()["error"]["code"] == "size_mismatch"
    )
    assert list(settings.upload.TMP_DIR.iterdir()) == []


async def test_missing_content_length(client, storage):
    async def chunks():
        yield make_image()

    response = await client.post(
        "/uploads", params={"path": path(storage, "a.jpg")}, content=chunks()
    )
    assert (
        response.status_code == 411
        and response.json()["error"]["code"] == "length_required"
    )


async def test_unknown_storage(client):
    response = await post_file(client, f"tcloud/{uuid.uuid4()}/a.jpg")
    assert response.status_code == 404


async def test_invalid_path(client, storage):
    response = await post_file(client, path(storage, "../a.jpg"))
    assert (
        response.status_code == 422
        and response.json()["error"]["code"] == "invalid_path"
    )


async def test_bad_callback_url(client, storage):
    response = await post_file(
        client, path(storage, "a.jpg"), callback_url="file:///etc/passwd"
    )
    assert (
        response.status_code == 422 and response.json()["error"]["code"] == "unsafe_url"
    )


async def test_conflict_error(client, storage):
    await upload(client, path(storage, "a.jpg"), make_image())
    response = await post_file(client, path(storage, "a.jpg"))
    error = response.json()["error"]
    assert response.status_code == 409 and error["code"] == "conflict"
    assert error["suggested_name"] == "a-2.jpg"


async def test_conflict_replace_keeps_uuid(client, telegram, storage):
    old = (await upload(client, path(storage, "a.jpg"), make_image()))["file"]
    new = (
        await upload(
            client,
            path(storage, "a.jpg"),
            make_image("JPEG", (10, 10)),
            on_conflict="replace",
        )
    )["file"]
    assert new["uuid"] == old["uuid"]
    assert new["width"] == 10 and new["updated_at"] > old["updated_at"]
    assert new["telegram"]["message_id"] != old["telegram"]["message_id"]
    assert len(telegram.messages()) == 1


async def test_conflict_keep_both(client, storage):
    await upload(client, path(storage, "a.jpg"), make_image())
    second = (
        await upload(
            client, path(storage, "a.jpg"), make_image(), on_conflict="keep_both"
        )
    )["file"]
    third = (
        await upload(
            client, path(storage, "a.jpg"), make_image(), on_conflict="keep_both"
        )
    )["file"]
    assert (second["name"], third["name"]) == ("a-2.jpg", "a-3.jpg")


async def test_conflict_skip(client, telegram, storage):
    first = (await upload(client, path(storage, "a.jpg"), make_image()))["file"]
    task = await upload(
        client, path(storage, "a.jpg"), make_image(), on_conflict="skip"
    )
    assert (
        task["status"] == "done"
        and task["file"]["uuid"] == first["uuid"]
        and task["warnings"]
    )
    assert len(telegram.messages()) == 1


async def test_second_upload_to_same_path_rejected(client, telegram, storage):
    telegram.delay = 0.5
    first = await start_upload(client, path(storage, "a.jpg"), make_image())
    response = await post_file(
        client, path(storage, "a.jpg"), make_image(), on_conflict="replace"
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "upload_in_progress"
    )
    assert (await wait_task(client, first))["status"] == "done"
    response = await post_file(
        client, path(storage, "a.jpg"), make_image(), on_conflict="replace"
    )
    assert response.status_code == 202


async def test_keep_both_without_extension(client, storage):
    await upload(client, path(storage, "README"), b"plain text")
    second = (
        await upload(
            client, path(storage, "README"), b"plain text", on_conflict="keep_both"
        )
    )["file"]
    assert second["name"] == "README-2"


async def test_keep_both_reserves_name_against_active_uploads(
    client, telegram, storage
):
    telegram.delay = 0.3
    names = []
    for options in ({}, {"on_conflict": "keep_both"}, {"on_conflict": "keep_both"}):
        response = await post_file(
            client, path(storage, "a.jpg"), make_image(), **options
        )
        names.append(response.json()["path"].rsplit("/", 1)[1])
    assert names == ["a.jpg", "a-2.jpg", "a-3.jpg"]


async def test_task_fields(client, storage):
    data = make_image()
    response = await post_file(client, path(storage, "a.jpg"), data, priority="high")
    task = response.json()
    assert (task["status"], task["stage"], task["priority"]) == (
        "queued",
        "scanning",
        "high",
    )
    assert task["bytes_total"] == len(data) and task["progress"] == 0.0
    assert task["waiting"] == {
        "reason": "queue:uploads.prepare",
        "code": "queue",
        "params": {"queue": "uploads.prepare"},
        "seconds": None,
    }
    done = await wait_task(client, task["id"])
    assert done["stage"] == "done" and done["error"] is None and not done["waiting"]
    assert (await client.get(f"/tasks/{uuid.uuid4()}")).status_code == 404


async def test_bad_priority(client, storage):
    response = await post_file(client, path(storage, "a.jpg"), priority="urgent")
    assert response.status_code == 422


async def test_tasks_survive_api_restart(make_client, telegram, storage):
    telegram.delay = 0.3
    first = await make_client()
    task_id = await start_upload(first, path(storage, "a.jpg"), make_image())
    second = await make_client()
    assert (await wait_task(second, task_id))["status"] == "done"


async def test_upload_from_url_with_own_name(client, telegram, storage, remote):
    Remote.files["/IMG_0001.JPG"] = make_image()
    body = {
        "url": f"{remote}/IMG_0001.JPG",
        "path": path(storage, "море.jpg"),
        "encrypt": False,
    }
    response = await client.post("/uploads/from-url", json=body)
    assert response.status_code == 202
    task = await wait_task(client, response.json()["id"])
    assert task["status"] == "done" and task["file"]["name"] == "море.jpg"
    assert telegram.messages()[0]["data"] == make_image()


async def test_upload_from_url_executable(client, telegram, storage, remote):
    Remote.files["/x.exe"] = EXE + b"\0" * 10000
    response = await client.post(
        "/uploads/from-url",
        json={"url": f"{remote}/x.exe", "path": path(storage, "x.exe")},
    )
    task = await wait_task(client, response.json()["id"])
    assert task["status"] == "done" and task["file"]["kind"] == "document", task
    assert len(telegram.messages()) == 1


async def test_upload_document_from_url(client, telegram, storage, remote):
    Remote.files["/doc"] = PDF
    response = await client.post(
        "/uploads/from-url",
        json={
            "url": f"{remote}/doc",
            "path": path(storage, "doc.pdf"),
            "encrypt": False,
        },
    )
    task = await wait_task(client, response.json()["id"])
    assert task["status"] == "done" and task["file"]["kind"] == "document"
    assert telegram.messages()[0]["data"] == PDF


async def test_upload_from_url_private_address_blocked(client, storage):
    body = {"url": "http://169.254.169.254/a.jpg", "path": path(storage, "x.jpg")}
    task = await wait_task(
        client, (await client.post("/uploads/from-url", json=body)).json()["id"]
    )
    assert task["error"]["code"] == "unsafe_url"


async def test_upload_from_url_bad_scheme(client, storage):
    body = {"url": "file:///etc/passwd", "path": path(storage, "x.jpg")}
    assert (await client.post("/uploads/from-url", json=body)).status_code == 422


async def test_webhook_signed(client, storage, remote):
    data = make_image()
    await start_upload(
        client, path(storage, "a.jpg"), data, callback_url=f"{remote}/hook"
    )
    for _ in range(200):
        if any(json.loads(body)["status"] == "done" for _, body in Remote.hooks):
            break
        await asyncio.sleep(0.02)
    stages = [json.loads(body)["stage"] for _, body in Remote.hooks]
    assert "done" in stages and "uploading_tg" in stages
    for headers, body in Remote.hooks:
        expected = "sha256=" + hmac.new(b"test", body, hashlib.sha256).hexdigest()
        assert headers["X-Tcloud-Signature"] == expected


async def test_batch_from_urls(client, storage, remote):
    Remote.files["/1.jpg"] = Remote.files["/2.jpg"] = make_image()
    items = [
        {"url": f"{remote}/1.jpg", "path": path(storage, "b/1.jpg")},
        {
            "url": f"{remote}/2.jpg",
            "path": path(storage, "b/2.jpg"),
            "priority": "high",
        },
        {"url": "ftp://example.com/x.exe", "path": path(storage, "b/setup.exe")},
    ]
    response = await client.post("/uploads/batch", json={"items": items})
    assert response.status_code == 202
    body = response.json()
    assert [item["error"] is None for item in body["items"]] == [True, True, False]
    assert body["items"][2]["error"]["code"] == "unsafe_url"
    assert body["items"][1]["task"]["priority"] == "high"
    for item in body["items"][:2]:
        assert item["task"]["batch_id"] == body["batch_id"]
        await wait_task(client, item["task"]["id"])
    batch = (await client.get("/tasks", params={"batch_id": body["batch_id"]})).json()
    assert batch["summary"]["total"] == 2
    assert batch["summary"]["by_status"] == {"done": 2}
    assert batch["summary"]["progress"] == 100.0 and batch["summary"]["finished"] == 2
    assert all(
        task["file"]["path"].startswith(path(storage, "b/")) for task in batch["items"]
    )


async def test_batch_validation(client, storage):
    assert (await client.post("/uploads/batch", json={"items": []})).status_code == 422
    no_url = {"path": path(storage, "a.jpg")}
    assert (
        await client.post("/uploads/batch", json={"items": [no_url]})
    ).status_code == 422
    many = [
        {"path": path(storage, f"{index}.jpg"), "url": "https://x.com/a.jpg"}
        for index in range(101)
    ]
    assert (
        await client.post("/uploads/batch", json={"items": many})
    ).status_code == 422


async def test_batch_same_path_twice(client, storage, remote):
    Remote.files["/a.jpg"] = make_image()
    item = {"url": f"{remote}/a.jpg", "path": path(storage, "a.jpg")}
    items = (await client.post("/uploads/batch", json={"items": [item, item]})).json()[
        "items"
    ]
    assert (
        items[0]["error"] is None and items[1]["error"]["code"] == "upload_in_progress"
    )
    await wait_task(client, items[0]["task"]["id"])


async def test_unknown_batch_is_empty(client):
    batch = (await client.get("/tasks", params={"batch_id": str(uuid.uuid4())})).json()
    assert batch["items"] == [] and batch["summary"]["total"] == 0
    assert (await client.get("/tasks")).status_code == 422
