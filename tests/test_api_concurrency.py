"""Сотни загрузок разом: лимиты стадий, очередь, пачки, место на диске, FloodWait, повторы."""

import shutil
import threading
import time
from collections import namedtuple

import pytest

from tcloud import gates
from tests.conftest import image, upload, wait_task


def p(storage, rest):
    return f"tcloud/{storage['id']}/{rest}"


def _start(client, path, data, **kw):
    task = client.post("/uploads", json={"path": path, "size": len(data), **kw}).json()
    assert client.put(f"/uploads/{task['id']}", content=data).status_code == 202
    return task["id"]


def test_hundred_parallel_uploads_respect_tg_limit(make_client, tg, storage):
    c = make_client(tg_concurrency=2)
    tg.delay = 0.02
    data = image()
    ids = [None] * 100

    def one(i):
        ids[i] = _start(c, p(storage, f"many/{i}.jpg"), data)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    results = [wait_task(c, tid, timeout=60) for tid in ids]
    assert all(r["status"] == "done" for r in results), [r["error"] for r in results if r["status"] != "done"][:3]
    assert tg.max_sending <= 2
    assert len(tg.messages()) == 100
    listing = c.get("/folders", params={"path": p(storage, "many")}).json()
    assert len(listing["files"]) == 100


def test_waiting_tasks_show_queue_position(make_client, tg, storage):
    c = make_client(tg_concurrency=1)
    tg.delay = 0.5
    ids = [_start(c, p(storage, f"{i}.jpg"), image()) for i in range(3)]
    deadline = time.monotonic() + 5
    positions = set()
    while time.monotonic() < deadline and len(positions) < 2:
        for tid in ids:
            t = c.get(f"/tasks/{tid}").json()
            if t["queue_position"]:
                assert t["waiting"] == {"reason": "queue:uploading_tg"}
                positions.add(t["queue_position"])
        time.sleep(0.02)
    assert positions == {1, 2}
    for tid in ids:
        assert wait_task(c, tid)["status"] == "done"


def test_flood_wait_pauses_and_resumes(make_client, tg, storage):
    c = make_client()
    tg.flood_once = 1
    tid = _start(c, p(storage, "a.jpg"), image())
    seen_wait = False
    for _ in range(100):
        t = c.get(f"/tasks/{tid}").json()
        if t["waiting"] and t["waiting"]["reason"] == "flood_wait":
            seen_wait = True
        if t["status"] == "done":
            break
        time.sleep(0.05)
    assert seen_wait and t["status"] == "done"


def test_network_error_retried(client, tg, storage):
    tg.fail_times = 2
    t = upload(client, p(storage, "a.jpg"), image())
    assert t["status"] == "done"


def test_network_error_gives_up(make_client, tg, storage):
    c = make_client(retries=1)
    tg.fail_times = 5
    t = upload(c, p(storage, "a.jpg"), image())
    assert t["status"] == "failed" and t["error"]["code"] == "internal"


def test_scanner_restart_retried(client, scanner, storage):
    calls = []
    orig = scanner.scan

    async def flaky(path):
        calls.append(1)
        if len(calls) == 1:
            scanner.available = False
        else:
            scanner.available = True
        await orig(path)

    scanner.scan = flaky
    t = upload(client, p(storage, "a.jpg"), image())
    assert t["status"] == "done" and len(calls) == 2


def test_too_many_active_tasks(make_client, storage):
    c = make_client(max_active_tasks=3)
    for i in range(3):
        assert c.post("/uploads", json={"path": p(storage, f"{i}.jpg"), "size": 10}).status_code == 201
    r = c.post("/uploads", json={"path": p(storage, "x.jpg"), "size": 10})
    assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_tasks"


def test_stale_pending_tasks_expire(make_client, storage):
    c = make_client(pending_ttl=0, max_active_tasks=1)
    old = c.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 10}).json()
    time.sleep(0.01)
    assert c.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 10}).status_code == 201
    assert c.get(f"/tasks/{old['id']}").json()["error"]["code"] == "expired"


Usage = namedtuple("Usage", "total used free")


def test_disk_full_rejects_upload(make_client, storage, monkeypatch):
    c = make_client(min_free_disk_mb=1)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(10**9, 10**9, gates.MiB + 100))
    r = c.post("/uploads", json={"path": p(storage, "a.jpg"), "size": 200})
    assert r.status_code == 507 and r.json()["error"]["code"] == "insufficient_storage"
    assert c.post("/uploads", json={"path": p(storage, "b.jpg"), "size": 50}).status_code == 201


def test_disk_budget_counts_reserved_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(0, 0, 1000))
    budget = gates.DiskBudget(tmp_path, min_free=100)
    r1 = budget.reserve(500)
    with pytest.raises(Exception):
        budget.reserve(500)  # 1000 - 500 - 500 < 100
    r1.update(400)  # 400 из 500 уже на диске — их видит disk_usage
    assert budget.free() == 900
    r1.release()
    budget.reserve(800)


# ---------- пачки ----------

def test_batch_upload(client, tg, storage):
    data = image()
    r = client.post("/uploads/batch", json={"items": [
        {"path": p(storage, "b/1.jpg"), "size": len(data)},
        {"path": p(storage, "b/2.jpg"), "size": len(data)},
        {"path": p(storage, "b/doc.pdf"), "size": 10},  # не медиа — ошибка только у этого пункта
    ]})
    assert r.status_code == 201
    body = r.json()
    items = body["items"]
    assert [i["error"] is None for i in items] == [True, True, False]
    assert items[2]["error"]["code"] == "unsupported_media_type"
    for i in items[:2]:
        assert i["task"]["batch_id"] == body["batch_id"]
        client.put(f"/uploads/{i['task']['id']}", content=data)
        wait_task(client, i["task"]["id"])
    batch = client.get("/tasks", params={"batch_id": body["batch_id"]}).json()
    assert batch["summary"]["total"] == 2 and batch["summary"]["by_status"] == {"done": 2}
    assert batch["summary"]["progress"] == 100.0 and batch["summary"]["finished"] == 2
    assert all(t["file"]["path"].startswith(p(storage, "b/")) for t in batch["items"])


def test_batch_validation(client, storage):
    assert client.post("/uploads/batch", json={"items": []}).status_code == 422
    assert client.post("/uploads/batch", json={"items": [{"path": "x"}]}).status_code == 422  # ни size, ни url
    both = {"path": p(storage, "a.jpg"), "size": 1, "url": "https://x.com/a.jpg"}
    assert client.post("/uploads/batch", json={"items": [both]}).status_code == 422
    many = [{"path": p(storage, f"{i}.jpg"), "size": 1} for i in range(101)]
    assert client.post("/uploads/batch", json={"items": many}).status_code == 422


def test_batch_same_path_twice(client, storage):
    item = {"path": p(storage, "a.jpg"), "size": 10}
    items = client.post("/uploads/batch", json={"items": [item, item]}).json()["items"]
    assert items[0]["error"] is None and items[1]["error"]["code"] == "upload_in_progress"


def test_list_tasks_filters(client, storage):
    upload(client, p(storage, "ok.jpg"), image())
    upload(client, p(storage, "bad.jpg"), b"MZ" + b"\0" * 100)
    client.post("/uploads", json={"path": p(storage, "wait.jpg"), "size": 10})
    all_ = client.get("/tasks").json()
    assert all_["summary"]["by_status"] == {"done": 1, "failed": 1, "pending": 1}
    assert [t["path"].rsplit("/", 1)[1] for t in all_["items"]] == ["wait.jpg", "bad.jpg", "ok.jpg"]
    failed = client.get("/tasks", params={"status": "failed"}).json()["items"]
    assert [t["error"]["code"] for t in failed] == ["unsupported_media_type"]
    assert len(client.get("/tasks", params={"limit": 1, "offset": 1}).json()["items"]) == 1
