import uuid

from tcloud import limits
from tests.conftest import KEY, image, upload


def test_wrong_api_key(client):
    r = client.get("/storages", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_no_key_header(make_client):
    c = make_client()
    c.headers.pop("X-API-Key")
    assert c.get("/health").status_code == 401


def test_health(client, scanner):
    assert client.get("/health").json() == {"ok": True, "db": True, "telegram": True, "antivirus": True}
    scanner.available = False
    assert client.get("/health").json()["ok"] is False


def test_account_limit(client, tg):
    assert client.get("/account").json() == {"premium": False, "file_limit": limits.FILE_MAX}
    tg.premium = True
    assert client.get("/account").json() == {"premium": True, "file_limit": limits.FILE_MAX_PREMIUM}


def test_create_storage(client, tg, storage):
    uuid.UUID(storage["id"])
    assert storage["name"] == "Фото" and storage["description"] == "мои фото"
    assert storage["path"] == f"tcloud/{storage['id']}"
    assert storage["available"] and not storage["has_photo"]
    assert storage["files"] == 0 and storage["total_size"] == 0
    assert storage["telegram"]["chat_id"] in tg.chans


def test_storage_name_validation(client):
    assert client.post("/storages", json={"name": ""}).status_code == 422
    assert client.post("/storages", json={"name": "x" * (limits.TITLE_MAX + 1)}).status_code == 422
    assert client.post("/storages", json={"name": "x", "description": "d" * (limits.ABOUT_MAX + 1)}).status_code == 422
    r = client.post("/storages", json={})
    assert r.json()["error"]["code"] == "validation_error"


def test_list_storages(client):
    a = client.post("/storages", json={"name": "A"}).json()
    b = client.post("/storages", json={"name": "B"}).json()
    listed = client.get("/storages").json()
    assert [s["id"] for s in listed] == [a["id"], b["id"]]
    assert [s["name"] for s in listed] == ["A", "B"]


def test_name_and_description_live_in_telegram(client, tg, storage):
    tg.chans[storage["telegram"]["chat_id"]]["title"] = "Переименовал в TG"
    assert client.get(f"/storages/{storage['id']}").json()["name"] == "Переименовал в TG"


def test_unavailable_storage(client, tg, storage):
    del tg.chans[storage["telegram"]["chat_id"]]
    s = client.get("/storages").json()[0]
    assert s["available"] is False and s["name"] is None


def test_patch_storage(client, tg, storage):
    r = client.patch(f"/storages/{storage['id']}", json={"name": "Новое", "description": "опис"})
    assert r.status_code == 200
    assert r.json()["name"] == "Новое" and r.json()["description"] == "опис"
    ch = tg.chans[storage["telegram"]["chat_id"]]
    assert ch["title"] == "Новое" and ch["about"] == "опис"
    r = client.patch(f"/storages/{storage['id']}", json={"description": ""})
    assert r.json()["name"] == "Новое" and r.json()["description"] == ""


def test_storage_not_found(client):
    r = client.get(f"/storages/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "storage_not_found"
    assert client.get("/storages/not-uuid").status_code == 422


def test_storage_photo(client, tg, storage):
    sid = storage["id"]
    assert client.get(f"/storages/{sid}/photo").status_code == 404
    r = client.put(f"/storages/{sid}/photo", content=image("PNG"))
    assert r.status_code == 200 and r.json()["has_photo"]
    assert client.get(f"/storages/{sid}/photo").content == tg.chans[storage["telegram"]["chat_id"]]["photo"]
    assert client.delete(f"/storages/{sid}/photo").json()["has_photo"] is False


def test_storage_photo_webp_converted_to_jpeg(client, tg, storage):
    client.put(f"/storages/{storage['id']}/photo", content=image("WEBP"))
    assert tg.chans[storage["telegram"]["chat_id"]]["photo"][:3] == b"\xff\xd8\xff"


def test_storage_photo_rejects_non_image(client, storage):
    assert client.put(f"/storages/{storage['id']}/photo", content=b"MZ" + b"\0" * 100).status_code == 415


def test_storage_photo_size_limit(client, storage, monkeypatch):
    monkeypatch.setattr(limits, "PHOTO_MAX", 100)
    r = client.put(f"/storages/{storage['id']}/photo", content=image("PNG", (300, 300)))
    assert r.status_code == 413


def test_storage_photo_scanned(client, storage, scanner):
    scanner.available = False
    assert client.put(f"/storages/{storage['id']}/photo", content=image()).status_code == 503


def test_storage_counts_files(client, storage):
    upload(client, f"tcloud/{storage['id']}/a.jpg", image())
    upload(client, f"tcloud/{storage['id']}/x/b.png", image("PNG"))
    s = client.get(f"/storages/{storage['id']}").json()
    assert s["files"] == 2 and s["total_size"] == len(image()) + len(image("PNG"))


# ---------- TCLOUD_CHATS ----------

def test_chats_from_env(make_client, tg):
    ok = tg.add_channel("ok")
    public = tg.add_channel("pub", public=True)
    not_admin = tg.add_channel("na", admin=False)
    chats = ",".join(map(str, (ok, public, not_admin, -100999)))
    c = make_client(chats=chats)
    ids = [s["telegram"]["chat_id"] for s in c.get("/storages").json()]
    assert ids == [ok]


def test_chats_from_env_idempotent(make_client, tg):
    cid = tg.add_channel("ok")
    make_client(chats=str(cid))
    c = make_client(chats=f"{cid}, {cid}")  # второй запуск
    assert len(c.get("/storages").json()) == 1


def test_no_chats_in_env(client):
    assert client.get("/storages").json() == []


def test_chats_env_validation():
    import pytest
    from pydantic import ValidationError

    from tcloud.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_key=KEY, tg_api_id=1, tg_api_hash="x", chats="Фото,-100123")
    assert Settings(_env_file=None, api_key=KEY, tg_api_id=1, tg_api_hash="x", chats=" -1001, -1002 ,").chat_ids == [-1001, -1002]
