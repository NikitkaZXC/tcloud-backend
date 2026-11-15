import asyncio
import hashlib
import hmac
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.config import settings
from src.infrastructure import limits
from src.infrastructure.integrations.web import client as web
from src.infrastructure.media import analyze as probe_module
from tests.api.conftest import KEY, path, start_upload, upload, wait_task
from tests.factories import EXE, make_audio, make_gif_animated, make_image, make_video


async def test_upload_photo_as_file(client, telegram, storage):
    data = make_image()
    task = await upload(client, path(storage, "отпуск/2026/photo.jpg"), data)
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
    root = (await client.get("/folders", params={"path": path(storage)})).json()
    assert [item["name"] for item in root["folders"]] == ["a"]
    nested = (
        await client.get("/folders", params={"path": path(storage, "a/b")})
    ).json()
    assert [item["name"] for item in nested["folders"]] == ["c"]


async def test_photo_as_media(client, telegram, storage):
    task = await upload(
        client, path(storage, "a.png"), make_image("PNG"), send_as="media"
    )
    assert task["file"]["sent_as"] == "media" and task["warnings"] == []
    assert telegram.messages()[0]["as_file"] is False


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


async def test_non_media_extension_rejected_before_upload(client, storage):
    response = await client.post(
        "/uploads", json={"path": path(storage, "doc.pdf"), "size": 10}
    )
    assert (
        response.status_code == 415
        and response.json()["error"]["code"] == "unsupported_media_type"
    )


async def test_spoofed_content_rejected(client, telegram, storage):
    task = await upload(client, path(storage, "virus.jpg"), EXE)
    assert (
        task["status"] == "failed" and task["error"]["code"] == "unsupported_media_type"
    )
    assert telegram.messages() == []


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


async def test_size_over_limit(client, telegram, storage):
    response = await client.post(
        "/uploads", json={"path": path(storage, "a.mp4"), "size": limits.FILE_MAX + 1}
    )
    assert (
        response.status_code == 413
        and response.json()["error"]["limit"] == limits.FILE_MAX
    )
    telegram.premium = True
    await client.get("/account")
    response = await client.post(
        "/uploads", json={"path": path(storage, "a.mp4"), "size": limits.FILE_MAX + 1}
    )
    assert response.status_code == 201
    big = {"path": path(storage, "b.mp4"), "size": limits.FILE_MAX_PREMIUM + 1}
    assert (await client.post("/uploads", json=big)).status_code == 413


async def test_body_bigger_than_declared(client, storage):
    data = make_image()
    task = (
        await client.post(
            "/uploads", json={"path": path(storage, "a.jpg"), "size": len(data) - 1}
        )
    ).json()
    assert (await client.put(f"/uploads/{task['id']}", content=data)).status_code == 413
    assert (await client.get(f"/tasks/{task['id']}")).json()["status"] == "failed"


async def test_body_smaller_than_declared(client, storage):
    data = make_image()
    task = (
        await client.post(
            "/uploads", json={"path": path(storage, "a.jpg"), "size": len(data) + 1}
        )
    ).json()
    response = await client.put(f"/uploads/{task['id']}", content=data)
    assert (
        response.status_code == 400
        and response.json()["error"]["code"] == "size_mismatch"
    )


async def test_put_twice(client, storage):
    data = make_image()
    task = (
        await client.post(
            "/uploads", json={"path": path(storage, "a.jpg"), "size": len(data)}
        )
    ).json()
    assert (await client.put(f"/uploads/{task['id']}", content=data)).status_code == 202
    response = await client.put(f"/uploads/{task['id']}", content=data)
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "task_not_pending"
    )


async def test_unknown_storage(client):
    response = await client.post(
        "/uploads", json={"path": f"tcloud/{uuid.uuid4()}/a.jpg", "size": 1}
    )
    assert response.status_code == 404


async def test_invalid_path(client, storage):
    response = await client.post(
        "/uploads", json={"path": path(storage, "../a.jpg"), "size": 1}
    )
    assert (
        response.status_code == 422
        and response.json()["error"]["code"] == "invalid_path"
    )


async def test_bad_callback_url(client, storage):
    body = {
        "path": path(storage, "a.jpg"),
        "size": 1,
        "callback_url": "file:///etc/passwd",
    }
    response = await client.post("/uploads", json=body)
    assert (
        response.status_code == 422 and response.json()["error"]["code"] == "unsafe_url"
    )


async def test_conflict_error(client, storage):
    await upload(client, path(storage, "a.jpg"), make_image())
    response = await client.post(
        "/uploads", json={"path": path(storage, "a.jpg"), "size": 10}
    )
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "conflict"
    )


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
    assert (second["name"], third["name"]) == ("a (1).jpg", "a (2).jpg")


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


async def test_second_upload_to_same_path_rejected(client, storage):
    first = (
        await client.post("/uploads", json={"path": path(storage, "a.jpg"), "size": 10})
    ).json()
    body = {"path": path(storage, "a.jpg"), "size": 10, "on_conflict": "replace"}
    response = await client.post("/uploads", json=body)
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "upload_in_progress"
    )
    assert first["status"] == "pending"


async def test_keep_both_reserves_name_against_active_uploads(client, storage):
    names = []
    for options in ({}, {"on_conflict": "keep_both"}, {"on_conflict": "keep_both"}):
        body = {"path": path(storage, "a.jpg"), "size": 10, **options}
        names.append(
            (await client.post("/uploads", json=body)).json()["path"].rsplit("/", 1)[1]
        )
    assert names == ["a.jpg", "a (1).jpg", "a (2).jpg"]


async def test_task_fields(client, storage):
    data = make_image()
    task = (
        await client.post(
            "/uploads", json={"path": path(storage, "a.jpg"), "size": len(data)}
        )
    ).json()
    assert (
        task["status"] == "pending"
        and task["stage"] == "receiving"
        and task["bytes_total"] == len(data)
    )
    assert task["progress"] == 0.0
    await client.put(f"/uploads/{task['id']}", content=data)
    done = await wait_task(client, task["id"])
    assert done["stage"] == "done" and done["error"] is None
    assert (await client.get(f"/tasks/{uuid.uuid4()}")).status_code == 404


async def test_restart_marks_running_tasks_failed(make_client, storage):
    first = await make_client()
    task = (
        await first.post("/uploads", json={"path": path(storage, "a.jpg"), "size": 10})
    ).json()
    second = await make_client()
    restarted = (await second.get(f"/tasks/{task['id']}")).json()
    assert (
        restarted["status"] == "failed" and restarted["error"]["code"] == "interrupted"
    )


class Remote(BaseHTTPRequestHandler):
    files: dict = {}
    hooks: list = []

    def do_GET(self):
        body = self.files.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.hooks.append((dict(self.headers), body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(web, "ip_allowed", lambda ip: True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Remote)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    Remote.files.clear()
    Remote.hooks.clear()


async def test_upload_from_url_with_own_name(client, telegram, storage, remote):
    Remote.files["/IMG_0001.JPG"] = make_image()
    body = {"url": f"{remote}/IMG_0001.JPG", "path": path(storage, "море.jpg")}
    response = await client.post("/uploads/from-url", json=body)
    assert response.status_code == 202
    task = await wait_task(client, response.json()["id"])
    assert task["status"] == "done" and task["file"]["name"] == "море.jpg"
    assert telegram.messages()[0]["data"] == make_image()


async def test_upload_from_url_non_media(client, telegram, storage, remote):
    Remote.files["/x.jpg"] = EXE + b"\0" * 10000
    response = await client.post(
        "/uploads/from-url",
        json={"url": f"{remote}/x.jpg", "path": path(storage, "x.jpg")},
    )
    task = await wait_task(client, response.json()["id"])
    assert (
        task["error"]["code"] == "unsupported_media_type" and telegram.messages() == []
    )


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
        expected = "sha256=" + hmac.new(KEY.encode(), body, hashlib.sha256).hexdigest()
        assert headers["X-Tcloud-Signature"] == expected
