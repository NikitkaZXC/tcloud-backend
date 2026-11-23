import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import main
from src.application.schemas import SettingsServiceRead
from tests.api.conftest import Remote, path, start_upload, upload, wait_task
from tests.factories import PDF, make_image

DEFAULTS = {
    "uploads": {
        "encrypt": True,
        "send_as": "file",
        "priority": "normal",
        "on_conflict": "error",
        "allow_unscannable": False,
        "webhook_url": None,
        "previews": True,
    },
    "archives": {"format": "zip", "priority": "normal"},
    "interface": {
        "language": None,
        "theme": "dark",
        "folder_view": "list",
        "sort_by": "name",
        "sort_order": "asc",
        "folders_first": True,
        "page_size": 20,
        "animations": True,
        "confirm_delete": True,
        "start_page": "dashboard",
        "tasks_filter": "all",
        "fx_sounds": False,
        "fx_easter_eggs": True,
        "dashboard_layout": [],
        "dashboard_hidden": [],
        "file_columns": {},
    },
    "telegram": {"chat_folder": True},
}


def code(response) -> str:
    return response.json()["error"]["code"]


async def patch(client, body: dict) -> dict:
    response = await client.patch("/account/settings", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_schema_defaults_match_docs():
    assert SettingsServiceRead().model_dump(mode="json") == DEFAULTS


async def test_dashboard_layout(client):
    saved = await patch(
        client, {"interface": {"dashboard_layout": ["tasks", "storages"]}}
    )
    assert saved["interface"]["dashboard_layout"] == ["tasks", "storages"]
    for layout in (["nope"], ["tasks", "tasks"], ["tasks"] * 9):
        response = await client.patch(
            "/account/settings", json={"interface": {"dashboard_layout": layout}}
        )
        assert response.status_code == 422 and code(response) == "validation_error"
    assert (await client.get("/account/settings")).json()["interface"][
        "dashboard_layout"
    ] == ["tasks", "storages"]


async def test_file_columns(client):
    saved = await patch(
        client, {"interface": {"file_columns": {"name": 320, "size": 48}}}
    )
    assert saved["interface"]["file_columns"] == {"name": 320, "size": 48}
    for columns in ({"owner": 100}, {"name": 47}, {"type": 2001}, {"name": "wide"}):
        response = await client.patch(
            "/account/settings", json={"interface": {"file_columns": columns}}
        )
        assert response.status_code == 422 and code(response) == "validation_error"
    assert (await client.get("/account/settings")).json()["interface"][
        "file_columns"
    ] == {"name": 320, "size": 48}


async def test_defaults(client):
    response = await client.get("/account/settings")
    assert response.status_code == 200 and response.json() == DEFAULTS


async def test_requires_sign_in(client):
    async with AsyncClient(
        transport=ASGITransport(main.app), base_url="http://test/api"
    ) as anonymous:
        for response in (
            await anonymous.get("/account/settings"),
            await anonymous.patch("/account/settings", json={}),
            await anonymous.delete("/account/settings"),
        ):
            assert response.status_code == 401
            assert code(response) == "unauthorized"


async def test_partial_patch_keeps_other_keys(client):
    first = await patch(client, {"uploads": {"priority": "high"}})
    assert first["uploads"]["priority"] == "high"
    second = await patch(
        client,
        {"uploads": {"send_as": "media"}, "interface": {"theme": "light"}},
    )
    expected = json.loads(json.dumps(DEFAULTS))
    expected["uploads"].update(priority="high", send_as="media")
    expected["interface"]["theme"] = "light"
    assert second == expected
    assert (await client.get("/account/settings")).json() == expected
    assert await patch(client, {}) == expected


async def test_concurrent_patches_keep_every_key(client):
    keys = ["fx_sounds", "animations", "confirm_delete", "folders_first"]
    for flag in (True, False) * 5:
        await asyncio.gather(
            *(patch(client, {"interface": {key: flag}}) for key in keys)
        )
        saved = (await client.get("/account/settings")).json()["interface"]
        assert {key: saved[key] for key in keys} == dict.fromkeys(keys, flag)


async def test_settings_in_me(client):
    await patch(client, {"interface": {"language": "en", "fx_sounds": True}})
    me = (await client.get("/users/me")).json()
    assert me["settings"]["interface"]["language"] == "en"
    assert me["settings"]["interface"]["fx_sounds"] is True
    assert me["settings"]["uploads"] == DEFAULTS["uploads"]


async def test_dashboard_hidden(client):
    saved = await patch(
        client, {"interface": {"dashboard_hidden": ["activity_map", "users"]}}
    )
    assert saved["interface"]["dashboard_hidden"] == ["activity_map", "users"]
    for hidden in (["nope"], ["users", "users"], ["users"] * 9, "users"):
        response = await client.patch(
            "/account/settings", json={"interface": {"dashboard_hidden": hidden}}
        )
        assert response.status_code == 422 and code(response) == "validation_error"
    assert (await client.get("/account/settings")).json()["interface"][
        "dashboard_hidden"
    ] == ["activity_map", "users"]


async def test_language_back_to_browser(client):
    await patch(client, {"interface": {"language": "ru"}})
    result = await patch(client, {"interface": {"language": None}})
    assert result["interface"]["language"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"unknown": {}},
        {"uploads": {"colour": "red"}},
        {"interface": {"fx_rainbow": True}},
        {"interface": {"page_size": 30}},
        {"interface": {"page_size": 500}},
        {"interface": {"page_size": "50"}},
        {"interface": {"activity_map": False}},
        {"interface": {"fx_pet": True}},
        {"interface": {"fx_confetti": True}},
        {"interface": {"fx_cursor": True}},
        {"uploads": {"send_as": "photo"}},
        {"uploads": {"priority": "urgent"}},
        {"uploads": {"on_conflict": "overwrite"}},
        {"uploads": {"encrypt": None}},
        {"uploads": {"encrypt": "maybe"}},
        {"uploads": None},
        {"archives": {"format": "rar"}},
        {"archives": {"priority": "max"}},
        {"interface": {"language": "de"}},
        {"interface": {"theme": "blue"}},
        {"interface": {"folder_view": "table"}},
        {"interface": {"sort_by": "owner"}},
        {"interface": {"sort_order": "up"}},
        {"interface": {"start_page": "files"}},
        {"interface": {"tasks_filter": "failed"}},
        {"uploads": {"webhook_url": "x" * 2049}},
    ],
)
async def test_validation_errors(client, body):
    response = await client.patch("/account/settings", json=body)
    assert response.status_code == 422 and code(response) == "validation_error"
    assert (await client.get("/account/settings")).json() == DEFAULTS


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/hook",
        "not a url",
        "http://127.0.0.1:8000/hook",
        "http://10.0.0.5/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/hook",
    ],
)
async def test_bad_webhook(client, url):
    response = await client.patch(
        "/account/settings", json={"uploads": {"webhook_url": url}}
    )
    assert response.status_code == 422 and code(response) == "unsafe_url"
    assert (await client.get("/account/settings")).json() == DEFAULTS


async def test_webhook_set_and_cleared(client):
    url = "https://hooks.example.com/tcloud?token=1"
    assert (await patch(client, {"uploads": {"webhook_url": url}}))["uploads"][
        "webhook_url"
    ] == url
    cleared = await patch(client, {"uploads": {"webhook_url": None}})
    assert cleared["uploads"]["webhook_url"] is None


async def test_reset(client):
    await patch(client, {"uploads": {"priority": "low"}, "archives": {"format": "7z"}})
    response = await client.delete("/account/settings")
    assert response.status_code == 200 and response.json() == DEFAULTS
    assert (await client.get("/account/settings")).json() == DEFAULTS


async def test_users_are_isolated(client, make_user):
    alice = await make_user("alice")
    bob = await make_user("bob")
    await patch(alice, {"interface": {"theme": "light"}})
    assert (await bob.get("/account/settings")).json() == DEFAULTS
    assert (await client.get("/account/settings")).json() == DEFAULTS
    await bob.delete("/account/settings")
    alice_settings = (await alice.get("/account/settings")).json()
    assert alice_settings["interface"]["theme"] == "light"


async def test_api_key_access(client, make_user):
    user = await make_user("scripted")
    created = await client.post(f"/users/{user.user_id}/api-keys", json={"name": "k"})
    headers = {"X-API-Key": created.json()["key"]}
    async with AsyncClient(
        transport=ASGITransport(main.app), base_url="http://test/api", headers=headers
    ) as keyed:
        result = await patch(keyed, {"uploads": {"priority": "low"}})
        assert result["uploads"]["priority"] == "low"
        assert (await keyed.get("/account/settings")).json() == result
    assert (await user.get("/account/settings")).json() == result


async def test_upload_uses_settings(client, telegram, storage):
    await patch(
        client,
        {"uploads": {"priority": "high", "encrypt": False, "on_conflict": "keep_both"}},
    )
    first = await upload(client, path(storage, "a.pdf"), PDF)
    assert first["priority"] == "high" and first["file"]["encrypted"] is False
    second = await upload(client, path(storage, "a.pdf"), PDF)
    assert second["status"] == "done" and second["file"]["name"] == "a-2.pdf"


async def test_explicit_params_win(client, storage):
    await patch(client, {"uploads": {"priority": "high", "encrypt": False}})
    task = await upload(
        client, path(storage, "a.pdf"), PDF, priority="low", encrypt="true"
    )
    assert task["priority"] == "low" and task["file"]["encrypted"] is True
    await patch(client, {"uploads": {"on_conflict": "skip"}})
    skipped = await upload(client, path(storage, "a.pdf"), PDF)
    assert skipped["warnings"] == ["skipped: file already exists"]
    assert skipped["warning_details"][0]["code"] == "skipped_exists"
    response = await client.post(
        "/uploads",
        params={"path": path(storage, "a.pdf"), "on_conflict": "error"},
        content=PDF,
    )
    assert response.status_code == 409 and code(response) == "conflict"


async def test_settings_media_turns_encryption_off(client, telegram, storage):
    await patch(client, {"uploads": {"send_as": "media", "encrypt": True}})
    task = await upload(client, path(storage, "a.jpg"), make_image())
    assert task["file"]["sent_as"] == "media" and task["file"]["encrypted"] is False
    assert telegram.messages()[0]["data"] == make_image()


async def test_explicit_encrypt_with_settings_media_fails(client, storage):
    await patch(client, {"uploads": {"send_as": "media"}})
    response = await client.post(
        "/uploads",
        params={"path": path(storage, "a.jpg"), "encrypt": "true"},
        content=make_image(),
    )
    assert response.status_code == 422 and code(response) == "encrypt_requires_file"


async def test_explicit_file_with_settings_media_is_encrypted(client, storage):
    await patch(client, {"uploads": {"send_as": "media"}})
    task = await upload(client, path(storage, "a.pdf"), PDF, send_as="file")
    assert task["file"]["sent_as"] == "file" and task["file"]["encrypted"] is True


async def test_allow_unscannable_from_settings(client, storage):
    await patch(client, {"uploads": {"allow_unscannable": True}})
    allowed = await upload(client, path(storage, "a.7z"), b"ENCRYPTED")
    assert allowed["status"] == "done"
    rejected = await upload(
        client, path(storage, "b.7z"), b"ENCRYPTED", allow_unscannable="false"
    )
    assert rejected["error"]["code"] == "unscannable"


async def hooked(timeout: float = 4) -> list[str]:
    for _ in range(int(timeout / 0.02)):
        if any(json.loads(body)["status"] == "done" for _, body in Remote.hooks):
            break
        await asyncio.sleep(0.02)
    return [json.loads(body)["path"] for _, body in Remote.hooks]


async def test_webhook_from_settings(client, storage, remote):
    await patch(client, {"uploads": {"webhook_url": f"{remote}/hook"}})
    await start_upload(client, path(storage, "a.jpg"), make_image())
    assert path(storage, "a.jpg") in await hooked()


async def test_empty_callback_url_disables_settings_webhook(client, storage, remote):
    await patch(client, {"uploads": {"webhook_url": f"{remote}/hook"}})
    task = await upload(client, path(storage, "a.jpg"), make_image(), callback_url="")
    assert task["status"] == "done"
    await asyncio.sleep(0.2)
    assert Remote.hooks == []


async def test_from_url_and_batch_use_settings(client, storage, remote):
    Remote.files["/a.jpg"] = Remote.files["/b.jpg"] = make_image()
    await patch(client, {"uploads": {"send_as": "media", "priority": "low"}})
    single = await client.post(
        "/uploads/from-url",
        json={"path": path(storage, "a.jpg"), "url": f"{remote}/a.jpg"},
    )
    assert single.status_code == 202, single.text
    task = await wait_task(client, single.json()["id"])
    assert task["priority"] == "low" and task["file"]["sent_as"] == "media"
    assert task["file"]["encrypted"] is False
    batch = await client.post(
        "/uploads/batch",
        json={
            "items": [
                {"path": path(storage, "b/1.jpg"), "url": f"{remote}/b.jpg"},
                {
                    "path": path(storage, "b/2.jpg"),
                    "url": f"{remote}/b.jpg",
                    "send_as": "file",
                    "priority": "high",
                },
                {
                    "path": path(storage, "b/3.jpg"),
                    "url": f"{remote}/b.jpg",
                    "encrypt": True,
                },
            ]
        },
    )
    assert batch.status_code == 202, batch.text
    first, second, third = batch.json()["items"]
    assert third["task"] is None
    assert third["error"]["code"] == "encrypt_requires_file"
    one = await wait_task(client, first["task"]["id"])
    two = await wait_task(client, second["task"]["id"])
    assert one["priority"] == "low" and one["file"]["sent_as"] == "media"
    assert two["priority"] == "high" and two["file"]["sent_as"] == "file"
    assert two["file"]["encrypted"] is True


async def test_archives_use_settings(client, storage):
    await upload(client, path(storage, "docs/a.pdf"), PDF)
    await patch(client, {"archives": {"format": "tar.gz", "priority": "high"}})
    response = await client.post("/folders/archive", params={"path": path(storage)})
    assert response.status_code == 202, response.text
    task = await wait_task(client, response.json()["id"])
    assert task["priority"] == "high" and task["archive"]["format"] == "tar.gz"
    assert task["archive"]["name"].endswith(".tar.gz")
    explicit = await client.post(
        "/folders/archive",
        params={"path": path(storage), "format": "zip", "priority": "low"},
    )
    task = await wait_task(client, explicit.json()["id"])
    assert task["priority"] == "low" and task["archive"]["format"] == "zip"


async def test_selection_archive_uses_settings(client, storage):
    file = (await upload(client, path(storage, "docs/a.pdf"), PDF))["file"]
    await patch(client, {"archives": {"format": "7z", "priority": "low"}})
    response = await client.post(
        "/folders/archive/selection", json={"files": [file["uuid"]], "name": "pick"}
    )
    assert response.status_code == 202, response.text
    task = await wait_task(client, response.json()["id"])
    assert task["priority"] == "low" and task["archive"]["name"] == "pick.7z"
    explicit = await client.post(
        "/folders/archive/selection",
        json={"files": [file["uuid"]], "format": "zip", "priority": "high"},
    )
    task = await wait_task(client, explicit.json()["id"])
    assert task["priority"] == "high" and task["archive"]["format"] == "zip"


@pytest.mark.parametrize("theme", ["system", "sunset", "navy", "lavender", "clay"])
async def test_theme_values(client, theme):
    result = await patch(client, {"interface": {"theme": theme}})
    assert result["interface"]["theme"] == theme
    assert (await client.get("/account/settings")).json()["interface"]["theme"] == theme


STALE = {
    "uploads": {"priority": "high", "removed_key": 1},
    "interface": {
        "theme": "retired-theme",
        "folder_view": "grid",
        "activity_map": False,
        "fx_pet": True,
        "fx_cursor": "junk",
    },
    "old_group": {"x": 1},
}


async def stored(engine, user_id: str) -> dict:
    async with engine.begin() as connection:
        return (
            await connection.execute(
                text("SELECT settings FROM users WHERE id = :id"), {"id": user_id}
            )
        ).scalar_one()


async def test_stale_stored_settings_do_not_break_login(engine, make_user, login_as):
    alice = await make_user("alice")
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE users SET settings = CAST(:value AS jsonb) WHERE id = :id"),
            {"value": json.dumps(STALE), "id": alice.user_id},
        )
    again = await login_as("alice", "alice-password")
    me = await again.get("/users/me")
    assert me.status_code == 200, me.text
    expected = json.loads(json.dumps(DEFAULTS))
    expected["uploads"]["priority"] = "high"
    expected["interface"]["folder_view"] = "grid"
    expected["interface"]["dashboard_hidden"] = ["activity_map"]
    assert me.json()["settings"] == expected
    assert (await again.get("/account/settings")).json() == expected
    assert await stored(engine, alice.user_id) == STALE
    response = await again.patch("/account/settings", json={"uploads": {"nope": 1}})
    assert response.status_code == 422 and code(response) == "validation_error"
    await patch(again, {"archives": {"priority": "low"}})
    assert await stored(engine, alice.user_id) == {
        "uploads": {"priority": "high"},
        "archives": {"priority": "low"},
        "interface": {"folder_view": "grid", "dashboard_hidden": ["activity_map"]},
    }
