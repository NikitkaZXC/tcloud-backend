import asyncio
import uuid

import main
from src.config import settings
from src.infrastructure import limits
from tests.api.conftest import path, upload
from tests.factories import EXE, PDF, make_image


async def test_wrong_api_key(client):
    response = await client.get("/storages", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_missing_api_key(client):
    client.headers.pop("X-API-Key")
    assert (await client.get("/health")).status_code == 401


async def healthy(client) -> dict:
    for _ in range(50):
        health = (await client.get("/health")).json()
        if health["ok"]:
            break
        await asyncio.sleep(0.05)
    return health


async def test_health(client, scanner, redis):
    assert await healthy(client) == {
        "ok": True,
        "db": True,
        "telegram": True,
        "telegram_worker": True,
        "telegram_download": True,
        "antivirus": True,
        "redis": True,
        "queue": True,
        "workers": {
            "uploads.download": True,
            "uploads.prepare": True,
            "uploads.telegram": True,
        },
    }
    scanner.available = False
    assert (await client.get("/health")).json()["ok"] is False


async def test_health_sees_dead_worker(client, monkeypatch):
    async def workers() -> set[str]:
        return {"uploads.download", "uploads.prepare"}

    monkeypatch.setattr(main.app.state.runtime.store, "workers", workers)
    health = (await client.get("/health")).json()
    assert health["ok"] is False and health["workers"]["uploads.telegram"] is False


async def test_account_limit(client, telegram):
    assert (await client.get("/account")).json() == {
        "premium": False,
        "file_limit": limits.FILE_MAX,
    }
    telegram.premium = True
    assert (await client.get("/account")).json() == {
        "premium": True,
        "file_limit": limits.FILE_MAX_PREMIUM,
    }


async def test_create_storage(storage, telegram):
    uuid.UUID(storage["id"])
    assert storage["name"] == "Фото" and storage["description"] == "мои фото"
    assert storage["path"] == f"tcloud/{storage['id']}"
    assert storage["available"] and not storage["has_photo"]
    assert storage["files"] == 0 and storage["total_size"] == 0
    assert storage["telegram"]["chat_id"] in telegram.chats


async def test_storage_name_validation(client):
    assert (await client.post("/storages", json={"name": ""})).status_code == 422
    assert (
        await client.post("/storages", json={"name": "x" * (limits.TITLE_MAX + 1)})
    ).status_code == 422
    long_about = {"name": "x", "description": "d" * (limits.ABOUT_MAX + 1)}
    assert (await client.post("/storages", json=long_about)).status_code == 422
    assert (await client.post("/storages", json={})).json()["error"][
        "code"
    ] == "validation_error"


async def test_list_storages(client):
    first = (await client.post("/storages", json={"name": "A"})).json()
    second = (await client.post("/storages", json={"name": "B"})).json()
    listed = (await client.get("/storages")).json()
    assert [item["id"] for item in listed] == [first["id"], second["id"]]
    assert [item["name"] for item in listed] == ["A", "B"]


async def test_name_lives_in_telegram(client, telegram, storage):
    telegram.chats[storage["telegram"]["chat_id"]]["title"] = "Переименовал в TG"
    assert (await client.get(f"/storages/{storage['id']}")).json()[
        "name"
    ] == "Переименовал в TG"


async def test_unavailable_storage(client, telegram, storage):
    del telegram.chats[storage["telegram"]["chat_id"]]
    listed = (await client.get("/storages")).json()[0]
    assert listed["available"] is False and listed["name"] is None


async def test_patch_storage(client, telegram, storage):
    response = await client.patch(
        f"/storages/{storage['id']}", json={"name": "Новое", "description": "опис"}
    )
    assert response.status_code == 200
    assert (
        response.json()["name"] == "Новое" and response.json()["description"] == "опис"
    )
    chat = telegram.chats[storage["telegram"]["chat_id"]]
    assert chat["title"] == "Новое" and chat["about"] == "опис"
    response = await client.patch(
        f"/storages/{storage['id']}", json={"description": ""}
    )
    assert response.json()["name"] == "Новое" and response.json()["description"] == ""


async def test_storage_not_found(client):
    response = await client.get(f"/storages/{uuid.uuid4()}")
    assert (
        response.status_code == 404
        and response.json()["error"]["code"] == "storage_not_found"
    )
    assert (await client.get("/storages/not-uuid")).status_code == 422


async def test_storage_photo(client, telegram, storage):
    storage_id = storage["id"]
    assert (await client.get(f"/storages/{storage_id}/photo")).status_code == 404
    response = await client.put(
        f"/storages/{storage_id}/photo", content=make_image("PNG")
    )
    assert response.status_code == 200 and response.json()["has_photo"]
    photo = (await client.get(f"/storages/{storage_id}/photo")).content
    assert photo == telegram.chats[storage["telegram"]["chat_id"]]["photo"]
    assert (await client.delete(f"/storages/{storage_id}/photo")).json()[
        "has_photo"
    ] is False


async def test_storage_photo_webp_converted_to_jpeg(client, telegram, storage):
    await client.put(f"/storages/{storage['id']}/photo", content=make_image("WEBP"))
    assert (
        telegram.chats[storage["telegram"]["chat_id"]]["photo"][:3] == b"\xff\xd8\xff"
    )


async def test_storage_photo_rejects_non_image(client, storage):
    assert (
        await client.put(f"/storages/{storage['id']}/photo", content=EXE)
    ).status_code == 415


async def test_storage_photo_size_limit(client, storage, monkeypatch):
    from src.application.services import storage as storage_module

    monkeypatch.setattr(storage_module, "PHOTO_MAX", 100)
    response = await client.put(
        f"/storages/{storage['id']}/photo", content=make_image("PNG", (300, 300))
    )
    assert response.status_code == 413


async def test_storage_photo_scanned(client, storage, scanner):
    scanner.available = False
    assert (
        await client.put(f"/storages/{storage['id']}/photo", content=make_image())
    ).status_code == 503


async def test_storage_counts_files(client, storage):
    await upload(client, path(storage, "a.jpg"), make_image())
    await upload(client, path(storage, "x/b.png"), make_image("PNG"))
    stats = (await client.get(f"/storages/{storage['id']}")).json()
    assert stats["files"] == 2 and stats["total_size"] == len(make_image()) + len(
        make_image("PNG")
    )


async def test_chats_from_env(make_client, telegram, monkeypatch):
    ok = telegram.add_channel("ok")
    public = telegram.add_channel("pub", public=True)
    not_admin = telegram.add_channel("na", admin=False)
    monkeypatch.setattr(
        settings.telegram, "CHATS", ",".join(map(str, (ok, public, not_admin, -100999)))
    )
    client = await make_client()
    assert [
        item["telegram"]["chat_id"] for item in (await client.get("/storages")).json()
    ] == [ok]


async def test_chats_from_env_idempotent(make_client, telegram, monkeypatch):
    chat_id = telegram.add_channel("ok")
    monkeypatch.setattr(settings.telegram, "CHATS", f"{chat_id}, {chat_id}")
    await make_client()
    client = await make_client()
    assert len((await client.get("/storages")).json()) == 1


async def test_no_chats_in_env(client):
    assert (await client.get("/storages")).json() == []


async def test_delete_storage_with_channel(client, telegram, storage):
    chat_id = storage["telegram"]["chat_id"]
    file = (await upload(client, path(storage, "a/b.pdf"), PDF))["file"]
    response = await client.delete(f"/storages/{storage['id']}")
    assert response.status_code == 204
    assert chat_id not in telegram.chats
    assert (await client.get(f"/storages/{storage['id']}")).status_code == 404
    assert (await client.get(f"/files/{file['uuid']}")).status_code == 404
    listing = await client.get("/folders", params={"path": path(storage, "a")})
    assert listing.status_code == 404
    again = await client.delete(f"/storages/{storage['id']}")
    assert again.json()["error"]["code"] == "storage_not_found"


async def test_delete_storage_keeps_channel(client, telegram, storage):
    chat_id = storage["telegram"]["chat_id"]
    await upload(client, path(storage, "b.pdf"), PDF)
    response = await client.delete(
        f"/storages/{storage['id']}", params={"keep_channel": "true"}
    )
    assert response.status_code == 204
    assert chat_id in telegram.chats and telegram.chats[chat_id]["messages"]
    assert (await client.get("/storages")).json() == []


async def test_delete_storage_with_uploads_in_progress(client, telegram, storage):
    store = main.app.state.runtime.store
    target, task_id = path(storage, "busy.pdf"), uuid.uuid4()
    await store.lock_path(target, task_id)
    response = await client.delete(f"/storages/{storage['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "storage_busy"
    assert storage["telegram"]["chat_id"] in telegram.chats
    await store.unlock_path(target, task_id)
    assert (await client.delete(f"/storages/{storage['id']}")).status_code == 204


async def test_delete_storage_whose_channel_is_gone(client, telegram, storage):
    telegram.chats.pop(storage["telegram"]["chat_id"])
    assert (await client.delete(f"/storages/{storage['id']}")).status_code == 204


async def test_channel_limit_is_reported(client, telegram):
    telegram.channels_full = True
    response = await client.post("/storages", json={"name": "Ещё одно"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "channels_limit"
    assert response.json()["error"]["limit"] == 500
    assert (await client.get("/storages")).json() == []
