import asyncio
import time
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import main
from src.config import settings
from src.infrastructure import limits
from tests.api.conftest import path, upload
from tests.factories import EXE, PDF, make_image


async def healthy(client) -> dict:
    for _ in range(50):
        health = (await client.get("/health")).json()
        if health["ok"]:
            break
        await asyncio.sleep(0.05)
    return health


async def test_health(client, scanner, redis):
    health = await healthy(client)
    details = health.pop("details")
    assert health == {
        "ok": True,
        "db": True,
        "telegram": True,
        "telegram_upload": True,
        "telegram_download": True,
        "antivirus": True,
        "redis": True,
        "queue": True,
        "workers": {
            "uploads.download": True,
            "uploads.prepare": True,
            "uploads.telegram": True,
            "archives.build": True,
        },
    }
    probes = ["db", "telegram", "telegram_upload", "telegram_download"]
    probes += ["antivirus", "redis", "queue"]
    queues = ["uploads.download", "uploads.prepare", "uploads.telegram"]
    queues += ["archives.build"]
    assert set(details) == {*probes, *queues}
    for name in probes:
        assert details[name]["latency_ms"] >= 0
        assert details[name]["last_seen_s"] is None
    for name in queues:
        assert details[name]["latency_ms"] is None
        assert (
            0 <= details[name]["last_seen_s"] < 3 * settings.upload.HEARTBEAT_INTERVAL
        )
    scanner.available = False
    assert (await client.get("/health")).json()["ok"] is False


async def test_health_sees_dead_worker(client, monkeypatch):
    async def workers() -> dict:
        return {
            "uploads.download": (time.time() - 2, None),
            "uploads.prepare": (time.time(), None),
        }

    monkeypatch.setattr(main.app.state.runtime.store, "workers", workers)
    health = (await client.get("/health")).json()
    assert health["ok"] is False and health["workers"]["uploads.telegram"] is False
    assert health["details"]["uploads.telegram"]["last_seen_s"] is None
    assert 2 <= health["details"]["uploads.download"]["last_seen_s"] < 3


async def test_health_uses_worker_telegram_state(client, monkeypatch):
    state = {"value": False}

    async def workers() -> dict:
        return {
            queue: (
                time.time(),
                state["value"] if queue == "uploads.telegram" else None,
            )
            for queue in (
                "uploads.download",
                "uploads.prepare",
                "uploads.telegram",
                "archives.build",
            )
        }

    monkeypatch.setattr(main.app.state.runtime.store, "workers", workers)
    health = (await client.get("/health")).json()
    assert health["telegram_upload"] is False and health["ok"] is False
    state["value"] = True
    health = (await client.get("/health")).json()
    assert health["telegram_upload"] is True and health["ok"] is True


async def test_public_health_without_sign_in(client, scanner):
    await healthy(client)
    async with AsyncClient(
        transport=ASGITransport(main.app), base_url="http://test/api"
    ) as anonymous:
        response = await anonymous.get("/health")
        assert response.status_code == 200 and response.json() == {"status": "ok"}
        scanner.available = False
        response = await anonymous.get("/health")
        assert response.status_code == 503
        assert response.json() == {"status": "degraded"}
        assert (await anonymous.get("/account")).status_code == 401
        bad = await anonymous.get("/health", headers={"X-API-Key": "nope"})
        assert bad.status_code == 401


async def test_account_limit(client, telegram, monkeypatch):
    monkeypatch.setattr(settings.upload, "CHANNELS_TTL", 0)
    telegram.other_channels = 3
    assert (await client.get("/account")).json() == {
        "telegram_connected": True,
        "premium": False,
        "file_limit": limits.FILE_MAX,
        "channels_used": 3,
        "channels_limit": limits.CHANNELS_MAX,
        "previews_enabled": True,
    }
    telegram.premium = True
    assert (await client.get("/account")).json() == {
        "telegram_connected": True,
        "premium": True,
        "file_limit": limits.FILE_MAX_PREMIUM,
        "channels_used": 3,
        "channels_limit": limits.CHANNELS_MAX_PREMIUM,
        "previews_enabled": True,
    }


async def test_account_before_telegram_login(client, telegram):
    telegram.authorized = False
    response = await client.get("/account")
    assert response.status_code == 200
    assert response.json() == {
        "telegram_connected": False,
        "premium": None,
        "file_limit": None,
        "channels_used": None,
        "channels_limit": None,
        "previews_enabled": True,
    }


async def test_account_channels_count_storages_and_other_chats(
    client, telegram, storage, monkeypatch
):
    monkeypatch.setattr(settings.upload, "CHANNELS_TTL", 0)
    telegram.other_channels = 10
    assert (await client.get("/account")).json()["channels_used"] == 11


async def test_account_channels_are_cached(client, telegram, monkeypatch):
    monkeypatch.setattr(settings.upload, "CHANNELS_TTL", 3600)
    telegram.other_channels = 5
    assert (await client.get("/account")).json()["channels_used"] == 5
    telegram.other_channels = 9
    assert (await client.get("/account")).json()["channels_used"] == 5
    assert telegram.dialogs_read == 1
    monkeypatch.setattr(settings.upload, "CHANNELS_TTL", 0)
    assert (await client.get("/account")).json()["channels_used"] == 9


async def test_account_channels_refresh_after_storage_change(
    client, telegram, monkeypatch
):
    monkeypatch.setattr(settings.upload, "CHANNELS_TTL", 3600)
    telegram.other_channels = 5
    assert (await client.get("/account")).json()["channels_used"] == 5
    created = await client.post("/storages", json={"name": "a"})
    assert (await client.get("/account")).json()["channels_used"] == 6
    deleted = await client.delete(f"/storages/{created.json()['id']}")
    assert deleted.status_code == 204
    assert (await client.get("/account")).json()["channels_used"] == 5


async def test_account_without_channels_count(client, telegram, monkeypatch):
    monkeypatch.setattr(settings.upload, "CHANNELS_TTL", 0)
    telegram.dialogs_flood = True
    response = await client.get("/account")
    assert response.status_code == 200
    assert response.json()["channels_used"] is None
    assert response.json()["channels_limit"] == limits.CHANNELS_MAX


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
    assert [(item["files"], item["total_size"]) for item in listed] == [(0, 0), (0, 0)]
    assert [(item["preview_count"], item["preview_bytes"]) for item in listed] == [
        (0, 0),
        (0, 0),
    ]


async def test_list_storages_counts_files(client):
    first = (await client.post("/storages", json={"name": "A"})).json()
    second = (await client.post("/storages", json={"name": "B"})).json()
    await upload(client, path(first, "a/1.pdf"), PDF)
    await upload(client, path(first, "2.jpg"), make_image())
    await upload(client, path(second, "deep/er/3.pdf"), PDF + b"x")
    listed = {item["id"]: item for item in (await client.get("/storages")).json()}
    assert (listed[first["id"]]["files"], listed[first["id"]]["total_size"]) == (
        2,
        len(PDF) + len(make_image()),
    )
    assert (listed[second["id"]]["files"], listed[second["id"]]["total_size"]) == (
        1,
        len(PDF) + 1,
    )


async def test_list_storages_counts_previews(client, engine, make_user):
    first = (await client.post("/storages", json={"name": "A"})).json()
    second = (await client.post("/storages", json={"name": "B"})).json()
    await upload(client, path(first, "a/1.jpg"), make_image(), encrypt="true")
    await upload(client, path(first, "2.jpg"), make_image(), encrypt="true")
    await upload(client, path(first, "3.jpg"), make_image(), encrypt="false")
    await upload(client, path(first, "4.pdf"), PDF, encrypt="true")
    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text(
                    "SELECT name, length(thumbnail) FROM files "
                    "WHERE thumbnail IS NOT NULL"
                )
            )
        ).all()
    assert len(stored) == 2
    listed = {item["id"]: item for item in (await client.get("/storages")).json()}
    assert (
        listed[first["id"]]["preview_count"],
        listed[first["id"]]["preview_bytes"],
    ) == (
        2,
        sum(size for _, size in stored),
    )
    assert (
        listed[second["id"]]["preview_count"],
        listed[second["id"]]["preview_bytes"],
    ) == (0, 0)
    one = (await client.get(f"/storages/{first['id']}")).json()
    assert one["preview_count"] == 2
    bob = await make_user("bob", "user", {"path": path(first, "a")})
    seen = (await bob.get(f"/storages/{first['id']}")).json()
    assert (seen["preview_count"], seen["preview_bytes"]) == (
        1,
        dict(stored)["1.jpg"],
    )


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


async def test_chat_folder_follows_storages(client, telegram):
    first = (await client.post("/storages", json={"name": "a"})).json()
    second = (await client.post("/storages", json={"name": "b"})).json()
    ids = [first["telegram"]["chat_id"], second["telegram"]["chat_id"]]
    assert telegram.folders == {"TCloud": ids}
    await client.delete(f"/storages/{first['id']}", params={"keep_channel": "true"})
    assert telegram.folders == {"TCloud": ids[1:]}


async def test_chat_folder_includes_attached_chats(make_client, telegram, monkeypatch):
    chat_id = telegram.add_channel("ok")
    monkeypatch.setattr(settings.telegram, "CHATS", str(chat_id))
    await make_client()
    assert telegram.folders == {"TCloud": [chat_id]}


async def test_chat_folder_setting(client, telegram, storage):
    chat_id = storage["telegram"]["chat_id"]
    off = await client.patch(
        "/account/settings", json={"telegram": {"chat_folder": False}}
    )
    assert off.status_code == 200 and telegram.folders == {}
    assert chat_id in telegram.chats
    await client.post("/storages", json={"name": "c"})
    assert telegram.folders == {}
    await client.delete("/account/settings")
    assert len(telegram.folders["TCloud"]) == 2


async def test_chat_folder_errors_do_not_fail_requests(client, telegram, storage):
    telegram.folder_fails = True
    created = await client.post("/storages", json={"name": "d"})
    assert created.status_code == 201
    assert (await client.delete(f"/storages/{storage['id']}")).status_code == 204
    response = await client.patch(
        "/account/settings", json={"telegram": {"chat_folder": False}}
    )
    assert response.status_code == 200


async def test_chat_folder_follows_root_settings_only(
    client, telegram, storage, make_user
):
    admin = await make_user("admin1", "admin")
    response = await admin.patch(
        "/account/settings", json={"telegram": {"chat_folder": False}}
    )
    assert response.status_code == 200
    assert telegram.folders == {"TCloud": [storage["telegram"]["chat_id"]]}


async def test_chat_folder_survives_stale_root_settings(client, telegram, engine):
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE users SET settings = CAST(:value AS jsonb) "
                "WHERE login = 'root'"
            ),
            {"value": '{"telegram": {"chat_folder": "maybe", "gone": 1}}'},
        )
    created = await client.post("/storages", json={"name": "s"})
    assert created.status_code == 201, created.text
    assert telegram.folders == {"TCloud": [created.json()["telegram"]["chat_id"]]}
    deleted = await client.delete(f"/storages/{created.json()['id']}")
    assert deleted.status_code == 204
    assert telegram.folders == {}


async def test_activity_shows_reads_and_writes(
    client, storage, make_user, redis, monkeypatch
):
    monkeypatch.setattr("src.infrastructure.cache.tasks.ACTIVITY_TAIL", 30)
    other = (await client.post("/storages", json={"name": "b"})).json()
    idle = {"read": False, "write": False}
    activity = (await client.get("/storages/activity")).json()
    assert {item["storage_id"]: {**item} for item in activity} == {
        storage["id"]: {"storage_id": storage["id"], **idle},
        other["id"]: {"storage_id": other["id"], **idle},
    }
    file = (await upload(client, path(storage, "a.pdf"), PDF))["file"]

    async def flags() -> dict:
        items = (await client.get("/storages/activity")).json()
        return {item["storage_id"]: (item["read"], item["write"]) for item in items}

    async def calm() -> None:
        keys = [key async for key in redis.scan_iter("*:activity:*")]
        await redis.delete(*keys)

    assert (await flags()) == {
        storage["id"]: (False, True),
        other["id"]: (False, False),
    }
    await calm()
    assert (await client.get(f"/files/{file['uuid']}/download")).status_code == 200
    assert (await flags())[storage["id"]] == (True, False)
    await calm()
    assert (await client.delete(f"/files/{file['uuid']}")).status_code == 204
    assert (await flags())[storage["id"]] == (False, True)
    stranger = await make_user("stranger")
    assert (await stranger.get("/storages/activity")).json() == []


async def test_activity_ends_soon_after_download(client, storage, monkeypatch):
    file = (await upload(client, path(storage, "a.pdf"), PDF))["file"]
    await asyncio.sleep(1.1)
    idle = {"storage_id": storage["id"], "read": False, "write": False}
    assert (await client.get("/storages/activity")).json() == [idle]
    assert (await client.get(f"/files/{file['uuid']}/download")).status_code == 200
    assert (await client.get("/storages/activity")).json()[0]["read"] is True
    await asyncio.sleep(1.1)
    assert (await client.get("/storages/activity")).json() == [idle]
