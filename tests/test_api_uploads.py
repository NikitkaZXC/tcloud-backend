import hashlib
import hmac
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tcloud import limits, security
from tests.conftest import KEY, audio, gif_animated, image, upload, video, wait_task


def p(storage, rest):
    return f"tcloud/{storage['id']}/{rest}"


def test_upload_photo_as_file(client, tg, storage):
    data = image()
    t = upload(client, p(storage, "отпуск/2026/photo.jpg"), data)
    assert t["status"] == "done" and t["progress"] == 100.0, t
    f = t["file"]
    assert f["path"] == p(storage, "отпуск/2026/photo.jpg")
    assert (f["size"], f["kind"], f["sent_as"], f["mime"]) == (len(data), "photo", "file", "image/jpeg")
    assert (f["width"], f["height"]) == (64, 48)
    assert f["sha256"] == hashlib.sha256(data).hexdigest()
    msg = tg.messages()[0]
    assert msg["data"] == data and msg["as_file"] is True and msg["name"] == "photo.jpg"
    # в подписи — только токен UUID для поиска в Telegram
    assert msg["caption"] == f"#tc{uuid.UUID(f['uuid']).hex}" == f["telegram"]["search"]
    assert len(msg["caption"]) <= limits.CAPTION_MAX
    assert f["telegram"]["message_id"] and f["telegram"]["link"].startswith("https://t.me/c/")


def test_upload_creates_parent_folders(client, storage):
    upload(client, p(storage, "a/b/c/x.jpg"), image())
    root = client.get("/folders", params={"path": f"tcloud/{storage['id']}"}).json()
    assert [f["name"] for f in root["folders"]] == ["a"]
    b = client.get("/folders", params={"path": p(storage, "a/b")}).json()
    assert [f["name"] for f in b["folders"]] == ["c"]


def test_photo_as_media(client, tg, storage):
    t = upload(client, p(storage, "a.png"), image("PNG"), send_as="media")
    assert t["file"]["sent_as"] == "media" and t["warnings"] == []
    assert tg.messages()[0]["as_file"] is False


def test_webp_as_media_converted_to_jpeg(client, tg, storage):
    t = upload(client, p(storage, "a.webp"), image("WEBP"), send_as="media")
    assert t["status"] == "done"
    assert tg.messages()[0]["data"][:3] == b"\xff\xd8\xff"
    assert t["file"]["sha256"] == hashlib.sha256(image("WEBP")).hexdigest()  # хеш оригинала


def test_big_photo_as_media_falls_back_to_file(client, tg, storage, monkeypatch):
    monkeypatch.setattr(limits, "PHOTO_MAX", 100)
    t = upload(client, p(storage, "big.jpg"), image(), send_as="media")
    assert t["file"]["sent_as"] == "file"
    assert "sent as file" in t["warnings"][0]
    assert tg.messages()[0]["as_file"] is True


def test_wide_photo_as_media_falls_back_to_file(client, storage):
    t = upload(client, p(storage, "pano.png"), image("PNG", (2100, 100)), send_as="media")
    assert t["file"]["sent_as"] == "file" and t["warnings"]


def test_video_as_media_has_attributes_and_thumb(client, tg, storage):
    t = upload(client, p(storage, "clip.mp4"), video("mp4", 640, 360), send_as="media")
    f = t["file"]
    assert (f["kind"], f["sent_as"], f["width"], f["height"]) == ("video", "media", 640, 360)
    assert 1.5 < f["duration"] < 2.5
    msg = tg.messages()[0]
    assert msg["media"].width == 640 and msg["as_file"] is False
    assert msg["thumb"][:3] == b"\xff\xd8\xff" and len(msg["thumb"]) <= limits.THUMB_MAX
    assert msg["path"].endswith(".mp4")  # Telethon определяет тип по расширению


def test_mkv_as_media_warns(client, storage):
    t = upload(client, p(storage, "clip.mkv"), video("mkv"), send_as="media")
    assert t["status"] == "done" and "x-matroska" in t["warnings"][0]


def test_audio(client, storage):
    t = upload(client, p(storage, "song.mp3"), audio("mp3"), send_as="media")
    assert t["file"]["kind"] == "audio" and t["file"]["duration"] > 1.5


def test_animation(client, storage):
    t = upload(client, p(storage, "fun.gif"), gif_animated(), send_as="media")
    assert t["file"]["kind"] == "animation"


# ---------- отказы ----------

def test_non_media_extension_rejected_before_upload(client, storage):
    r = client.post("/uploads", json={"path": p(storage, "doc.pdf"), "size": 10})
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_media_type"


def test_spoofed_content_rejected(client, tg, storage):
    t = upload(client, p(storage, "virus.jpg"), b"MZ" + b"\0" * 5000)
    assert t["status"] == "failed" and t["error"]["code"] == "unsupported_media_type"
    assert tg.messages() == []


def test_extension_mismatch(client, storage):
    t = upload(client, p(storage, "photo.mp4"), image())
    assert t["error"]["code"] == "extension_mismatch"


def test_infected_rejected(client, tg, storage):
    t = upload(client, p(storage, "a.jpg"), image() + b"EICAR")
    assert t["status"] == "failed" and t["error"]["code"] == "infected"
    assert tg.messages() == []


def test_scanner_down_fails_closed(client, tg, scanner, storage):
    scanner.available = False
    t = upload(client, p(storage, "a.jpg"), image())
    assert t["error"]["code"] == "scanner_unavailable" and tg.messages() == []


def test_size_over_limit(client, tg, storage):
    r = client.post("/uploads", json={"path": p(storage, "a.mp4"), "size": limits.FILE_MAX + 1})
    assert r.status_code == 413 and r.json()["error"]["limit"] == limits.FILE_MAX
    tg.premium = True
    client.get("/account")  # Premium кешируется на минуту, /account обновляет
    r = client.post("/uploads", json={"path": p(storage, "a.mp4"), "size": limits.FILE_MAX + 1})
    assert r.status_code == 201
    r = client.post("/uploads", json={"path": p(storage, "b.mp4"), "size": limits.FILE_MAX_PREMIUM + 1})
    assert r.status_code == 413


def test_body_bigger_than_declared(client, storage):
    data = image()
    task = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": len(data) - 1}).json()
    r = client.put(f"/uploads/{task['id']}", content=data)
    assert r.status_code == 413
    assert client.get(f"/tasks/{task['id']}").json()["status"] == "failed"


def test_body_smaller_than_declared(client, storage):
    data = image()
    task = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": len(data) + 1}).json()
    r = client.put(f"/uploads/{task['id']}", content=data)
    assert r.status_code == 400 and r.json()["error"]["code"] == "size_mismatch"


def test_put_twice(client, storage):
    data = image()
    task = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": len(data)}).json()
    assert client.put(f"/uploads/{task['id']}", content=data).status_code == 202
    r = client.put(f"/uploads/{task['id']}", content=data)
    assert r.status_code == 409 and r.json()["error"]["code"] == "task_not_pending"


def test_unknown_storage(client):
    r = client.post("/uploads", json={"path": f"tcloud/{uuid.uuid4()}/a.jpg", "size": 1})
    assert r.status_code == 404


def test_invalid_path(client, storage):
    r = client.post("/uploads", json={"path": p(storage, "../a.jpg"), "size": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_path"


def test_bad_callback_url(client, storage):
    r = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 1, "callback_url": "file:///etc/passwd"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "unsafe_url"


def test_long_flood_wait_fails(make_client, tg, storage):
    c = make_client(tg_flood_max_wait=100)
    tg.flood = 300
    t = upload(c, p(storage, "a.jpg"), image())
    assert t["error"] == {"code": "flood_wait", "message": "Telegram asks to wait 300 s", "retry_after": 300}


# ---------- конфликты ----------

def test_conflict_error(client, storage):
    upload(client, p(storage, "a.jpg"), image())
    r = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 10})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


def test_conflict_replace_keeps_uuid(client, tg, storage):
    old = upload(client, p(storage, "a.jpg"), image())["file"]
    new = upload(client, p(storage, "a.jpg"), image("JPEG", (10, 10)), on_conflict="replace")["file"]
    assert new["uuid"] == old["uuid"]
    assert new["width"] == 10 and new["updated_at"] > old["updated_at"]
    assert new["telegram"]["message_id"] != old["telegram"]["message_id"]
    assert len(tg.messages()) == 1  # старое сообщение удалено


def test_conflict_keep_both(client, storage):
    upload(client, p(storage, "a.jpg"), image())
    second = upload(client, p(storage, "a.jpg"), image(), on_conflict="keep_both")["file"]
    third = upload(client, p(storage, "a.jpg"), image(), on_conflict="keep_both")["file"]
    assert (second["name"], third["name"]) == ("a (1).jpg", "a (2).jpg")


def test_conflict_skip(client, tg, storage):
    first = upload(client, p(storage, "a.jpg"), image())["file"]
    t = upload(client, p(storage, "a.jpg"), image(), on_conflict="skip")
    assert t["status"] == "done" and t["file"]["uuid"] == first["uuid"] and t["warnings"]
    assert len(tg.messages()) == 1


def test_second_upload_to_same_path_rejected(client, storage):
    first = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 10}).json()
    r = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 10, "on_conflict": "replace"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "upload_in_progress"
    assert first["status"] == "pending"


def test_keep_both_reserves_name_against_active_uploads(client, storage):
    a = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 10}).json()
    b = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 10, "on_conflict": "keep_both"}).json()
    c = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 10, "on_conflict": "keep_both"}).json()
    assert [x["path"].rsplit("/", 1)[1] for x in (a, b, c)] == ["a.jpg", "a (1).jpg", "a (2).jpg"]


# ---------- задачи и прогресс ----------

def test_task_fields(client, storage):
    data = image()
    task = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": len(data)}).json()
    assert task["status"] == "pending" and task["stage"] == "receiving" and task["bytes_total"] == len(data)
    assert task["progress"] == 0.0
    client.put(f"/uploads/{task['id']}", content=data)
    done = wait_task(client, task["id"])
    assert done["stage"] == "done" and done["error"] is None
    assert client.get(f"/tasks/{uuid.uuid4()}").status_code == 404


def test_restart_marks_running_tasks_failed(make_client, storage):
    c = make_client()
    task = c.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 10}).json()
    c2 = make_client()  # «рестарт»
    t = c2.get(f"/tasks/{task['id']}").json()
    assert t["status"] == "failed" and t["error"]["code"] == "interrupted"


# ---------- по ссылке и вебхук ----------

class Handler(BaseHTTPRequestHandler):
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

    def log_message(self, *a):
        pass


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(security, "ip_allowed", lambda ip: True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    Handler.files.clear()
    Handler.hooks.clear()


def test_upload_from_url_with_own_name(client, tg, storage, remote):
    Handler.files["/IMG_0001.JPG"] = image()
    r = client.post("/uploads/from-url", json={"url": f"{remote}/IMG_0001.JPG", "path": p(storage, "море.jpg")})
    assert r.status_code == 202
    t = wait_task(client, r.json()["id"])
    assert t["status"] == "done" and t["file"]["name"] == "море.jpg"
    assert tg.messages()[0]["data"] == image()


def test_upload_from_url_non_media(client, tg, storage, remote):
    Handler.files["/x.jpg"] = b"MZ" + b"\0" * 10000
    r = client.post("/uploads/from-url", json={"url": f"{remote}/x.jpg", "path": p(storage, "x.jpg")})
    t = wait_task(client, r.json()["id"])
    assert t["error"]["code"] == "unsupported_media_type" and tg.messages() == []


def test_upload_from_url_private_address_blocked(client, storage):
    r = client.post("/uploads/from-url", json={"url": "http://169.254.169.254/a.jpg", "path": p(storage, "x.jpg")})
    t = wait_task(client, r.json()["id"])
    assert t["error"]["code"] == "unsafe_url"


def test_upload_from_url_bad_scheme(client, storage):
    r = client.post("/uploads/from-url", json={"url": "file:///etc/passwd", "path": p(storage, "x.jpg")})
    assert r.status_code == 422


def test_webhook_signed(client, storage, remote):
    data = image()
    task = client.post("/uploads", json={"path": p(storage, "a.jpg"), "size": len(data), "callback_url": f"{remote}/hook"}).json()
    client.put(f"/uploads/{task['id']}", content=data)
    wait_task(client, task["id"])
    import time

    for _ in range(100):
        if any(json.loads(b)["status"] == "done" for _, b in Handler.hooks):
            break
        time.sleep(0.05)
    stages = [json.loads(b)["stage"] for _, b in Handler.hooks]
    assert "done" in stages and "uploading_tg" in stages
    headers, body = Handler.hooks[-1]
    expected = "sha256=" + hmac.new(KEY.encode(), body, hashlib.sha256).hexdigest()
    sigs = {h.get("X-TCloud-Signature") for h, _ in Handler.hooks}
    assert sigs and all(s and s.startswith("sha256=") for s in sigs)
    assert any(h["X-TCloud-Signature"] == expected for h, _ in [(headers, body)])
