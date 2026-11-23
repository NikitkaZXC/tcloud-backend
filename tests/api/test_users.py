import io
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text

import main
from src.application.bootstrap.startup import prepare
from src.config import settings
from tests.api.conftest import (
    build_archive,
    listing,
    path,
    start_upload,
    upload,
    wait_task,
)
from tests.factories import PDF, make_image


def code(response) -> str:
    return response.json()["error"]["code"]


@pytest.fixture
async def anonymous(client):
    other = AsyncClient(transport=ASGITransport(main.app), base_url="http://test/api")
    yield other
    await other.aclose()


async def test_requests_need_sign_in(anonymous):
    for response in (
        await anonymous.get("/account"),
        await anonymous.get("/storages"),
        await anonymous.get("/telegram"),
    ):
        assert response.status_code == 401 and code(response) == "unauthorized"


async def test_root_comes_from_env(client):
    me = (await client.get("/users/me")).json()
    assert me["login"] == "root" and me["role"] == "root" and me["grants"] == []


async def test_wrong_password(anonymous, client):
    response = await anonymous.post(
        "/auth/signin", json={"login": "root", "password": "wrong-password"}
    )
    assert response.status_code == 401 and code(response) == "invalid_credentials"
    response = await anonymous.post(
        "/auth/signin", json={"login": "nobody", "password": "wrong-password"}
    )
    assert response.status_code == 401 and code(response) == "invalid_credentials"


async def test_lock_after_failed_logins(anonymous, make_user):
    await make_user("victim")
    for _ in range(settings.jwt.MAX_FAILED_LOGINS - 1):
        await anonymous.post(
            "/auth/signin", json={"login": "victim", "password": "wrong-password"}
        )
    response = await anonymous.post(
        "/auth/signin", json={"login": "victim", "password": "wrong-password"}
    )
    assert response.status_code == 429 and code(response) == "account_locked"
    response = await anonymous.post(
        "/auth/signin", json={"login": "victim", "password": "victim-password"}
    )
    assert response.status_code == 429 and response.json()["error"]["retry_after"] > 0


async def test_cookies_are_http_only(anonymous, client):
    response = await anonymous.post(
        "/auth/signin", json={"login": "root", "password": "root-password"}
    )
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2 and all("HttpOnly" in cookie for cookie in cookies)


async def test_refresh_rotates_and_detects_reuse(anonymous, client):
    await anonymous.post(
        "/auth/signin", json={"login": "root", "password": "root-password"}
    )
    old_refresh = anonymous.cookies["refresh"]
    assert (await anonymous.post("/auth/refresh")).status_code == 204
    assert anonymous.cookies["refresh"] != old_refresh
    assert (await anonymous.get("/users/me")).status_code == 200
    anonymous.cookies.set("refresh", old_refresh)
    response = await anonymous.post("/auth/refresh")
    assert response.status_code == 401 and code(response) == "invalid_refresh_token"
    response = await client.get("/users/me")
    assert response.status_code == 401 and code(response) == "token_invalid"


async def test_logout_revokes_access(make_user):
    user = await make_user("leaver")
    assert (await user.get("/users/me")).status_code == 200
    access = user.cookies["access"]
    assert (await user.post("/auth/logout")).status_code == 204
    user.cookies.set("access", access)
    assert (await user.get("/users/me")).status_code == 401


async def test_sessions_and_logout_all(make_user, login_as):
    first = await make_user("multi")
    second = await login_as("multi", "multi-password")
    sessions = (await first.get("/auth/sessions")).json()
    assert len(sessions) == 2 and sum(item["is_current"] for item in sessions) == 1
    other = next(item for item in sessions if not item["is_current"])
    assert (await first.delete(f"/auth/sessions/{other['id']}")).status_code == 204
    assert (await second.get("/users/me")).status_code == 401
    assert (await first.post("/auth/logout/all")).status_code == 204
    assert (await first.get("/users/me")).status_code == 401


async def test_change_password(make_user, anonymous):
    user = await make_user("changer")
    response = await user.post(
        "/auth/password",
        json={"current_password": "wrong", "new_password": "new-password"},
    )
    assert response.status_code == 401
    response = await user.post(
        "/auth/password",
        json={"current_password": "changer-password", "new_password": "new-password"},
    )
    assert response.status_code == 204
    assert (await user.get("/users/me")).status_code == 200
    response = await anonymous.post(
        "/auth/signin", json={"login": "changer", "password": "new-password"}
    )
    assert response.status_code == 204


async def test_no_self_signup(anonymous):
    response = await anonymous.post(
        "/users", json={"login": "intruder", "password": "intruder-password"}
    )
    assert response.status_code == 401


async def test_roles_hierarchy(client, make_user):
    admin = await make_user("boss", "admin")
    user = await make_user("worker")
    response = await user.post(
        "/users", json={"login": "x-user", "password": "x-password"}
    )
    assert response.status_code == 403 and code(response) == "forbidden"
    response = await admin.post(
        "/users", json={"login": "x-user", "password": "x-password"}
    )
    assert response.status_code == 201
    response = await admin.post(
        "/users", json={"login": "x-admin", "password": "x-password", "role": "admin"}
    )
    assert response.status_code == 403
    response = await admin.patch(f"/users/{user.user_id}", json={"role": "admin"})
    assert response.status_code == 403
    other_admin = (
        await client.post(
            "/users",
            json={"login": "boss2", "password": "boss2-password", "role": "admin"},
        )
    ).json()
    assert (await admin.delete(f"/users/{other_admin['id']}")).status_code == 403
    root_id = (await client.get("/users/me")).json()["id"]
    assert (
        await admin.patch(f"/users/{root_id}", json={"blocked": True})
    ).status_code == 403
    assert (await client.delete(f"/users/{root_id}")).status_code == 403
    response = await client.post(
        "/users", json={"login": "root2", "password": "x-password", "role": "root"}
    )
    assert response.status_code == 422
    response = await admin.post(
        "/users", json={"login": "x-user", "password": "x-password"}
    )
    assert response.status_code == 409 and code(response) == "user_exists"
    assert (await user.get("/telegram")).status_code == 403
    assert (await admin.get("/telegram")).status_code == 403
    assert (await client.get("/telegram")).status_code == 200


async def test_block_logs_user_out(client, make_user):
    user = await make_user("blocked")
    response = await client.patch(f"/users/{user.user_id}", json={"blocked": True})
    assert response.status_code == 200 and response.json()["blocked"] is True
    assert (await user.get("/users/me")).status_code == 401
    other = AsyncClient(transport=ASGITransport(main.app), base_url="http://test/api")
    response = await other.post(
        "/auth/signin", json={"login": "blocked", "password": "blocked-password"}
    )
    await other.aclose()
    assert response.status_code == 403 and code(response) == "user_blocked"


async def test_api_keys(client, make_user, anonymous):
    user = await make_user("scripted")
    response = await user.post(f"/users/{user.user_id}/api-keys", json={"name": "x"})
    assert response.status_code == 403
    created = await client.post(
        f"/users/{user.user_id}/api-keys", json={"name": "backup script"}
    )
    assert created.status_code == 201
    key = created.json()["key"]
    assert key.startswith("tc_")
    listed = (await client.get(f"/users/{user.user_id}/api-keys")).json()
    assert [item["key"] for item in listed] == [None]
    me = await anonymous.get("/users/me", headers={"X-API-Key": key})
    assert me.status_code == 200 and me.json()["login"] == "scripted"
    response = await anonymous.get("/users/me", headers={"X-API-Key": "tc_wrong"})
    assert response.status_code == 401
    assert (
        await client.delete(f"/users/{user.user_id}/api-keys/{created.json()['id']}")
    ).status_code == 204
    assert (
        await anonymous.get("/users/me", headers={"X-API-Key": key})
    ).status_code == 401


async def test_grants_limit_files_and_folders(client, storage, make_user):
    await upload(client, path(storage, "shared/a.pdf"), PDF)
    await upload(client, path(storage, "shared/deep/b.pdf"), PDF)
    secret = (await upload(client, path(storage, "secret/c.pdf"), PDF))["file"]
    reader = await make_user("reader", "user", {"path": path(storage, "shared")})
    assert [item["id"] for item in (await reader.get("/storages")).json()] == [
        storage["id"]
    ]
    root = await listing(reader, path(storage))
    assert [folder["path"] for folder in root["folders"]] == [path(storage, "shared")]
    assert root["files"] == []
    deep = await listing(reader, path(storage, "shared/deep"))
    assert [file["name"] for file in deep["files"]] == ["b.pdf"]
    response = await reader.get("/folders", params={"path": path(storage, "secret")})
    assert response.status_code == 403
    assert (await reader.get(f"/files/{secret['uuid']}")).status_code == 403
    assert (await reader.get(f"/files/{secret['uuid']}/download")).status_code == 403
    task, archive = await build_archive(reader, path(storage))
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as content:
        assert sorted(content.namelist()) == ["shared/a.pdf", "shared/deep/b.pdf"]
    assert (await client.get(f"/tasks/{task['id']}/archive")).status_code == 404
    assert (await reader.delete(f"/files/{secret['uuid']}")).status_code == 403
    response = await reader.post(
        "/uploads", params={"path": path(storage, "shared/new.pdf")}, content=PDF
    )
    assert response.status_code == 403
    response = await reader.post("/folders", json={"path": path(storage, "shared/x")})
    assert response.status_code == 403
    assert (await reader.post("/storages", json={"name": "mine"})).status_code == 403
    assert (await reader.delete(f"/storages/{storage['id']}")).status_code == 403


async def test_folder_only_grant_with_upload_and_delete(client, storage, make_user):
    await upload(client, path(storage, "inbox/sub/hidden.pdf"), PDF)
    writer = await make_user(
        "writer",
        "user",
        {
            "path": path(storage, "inbox"),
            "recursive": False,
            "can_upload": True,
            "can_delete": True,
        },
    )
    task = await upload(writer, path(storage, "inbox/mine.jpg"), make_image())
    assert task["status"] == "done", task
    page = await listing(writer, path(storage, "inbox"))
    assert page["folders"] == [] and [f["name"] for f in page["files"]] == ["mine.jpg"]
    response = await writer.post(
        "/uploads", params={"path": path(storage, "inbox/sub/x.pdf")}, content=PDF
    )
    assert response.status_code == 403
    assert (
        await writer.delete("/folders", params={"path": path(storage, "inbox")})
    ).status_code == 403
    assert (await writer.delete(f"/files/{task['file']['uuid']}")).status_code == 204


async def test_grant_changes_apply_at_once(client, storage, make_user):
    user = await make_user("later")
    target = path(storage, "late")
    assert (
        await user.get("/folders", params={"path": path(storage)})
    ).status_code == 403
    grant = (
        await client.put(f"/users/{user.user_id}/grants", json={"path": target})
    ).json()
    assert grant["folder"] == "late" and grant["can_read"] and not grant["can_upload"]
    assert (
        await user.get("/folders", params={"path": path(storage)})
    ).status_code == 200
    replaced = await client.put(
        f"/users/{user.user_id}/grants", json={"path": target, "can_upload": True}
    )
    assert replaced.json()["id"] == grant["id"] and replaced.json()["can_upload"]
    me = (await user.get("/users/me")).json()
    assert [item["id"] for item in me["grants"]] == [grant["id"]]
    assert (
        await client.delete(f"/users/{user.user_id}/grants/{grant['id']}")
    ).status_code == 204
    assert (
        await user.get("/folders", params={"path": path(storage)})
    ).status_code == 403


async def test_users_see_only_own_tasks(client, storage, make_user):
    grant = {"path": path(storage, "box"), "can_upload": True}
    alice = await make_user("alice", "user", grant)
    bob = await make_user("bob", "user", grant)
    task_id = await start_upload(alice, path(storage, "box/a.pdf"), PDF)
    task = await wait_task(alice, task_id)
    assert task["user_id"] == alice.user_id
    response = await bob.get(f"/tasks/{task_id}")
    assert response.status_code == 404 and code(response) == "task_not_found"
    assert (await bob.post(f"/tasks/{task_id}/retry")).status_code == 404
    assert (await client.get(f"/tasks/{task_id}")).status_code == 200


async def test_replace_needs_delete_right(client, storage, make_user):
    await upload(client, path(storage, "up/a.pdf"), PDF)
    user = await make_user(
        "uploader", "user", {"path": path(storage, "up"), "can_upload": True}
    )
    response = await user.post(
        "/uploads",
        params={"path": path(storage, "up/a.pdf"), "on_conflict": "replace"},
        content=PDF,
    )
    assert response.status_code == 403


async def test_root_password_follows_env(client, anonymous, monkeypatch):
    monkeypatch.setattr(settings.root, "PASSWORD", SecretStr("changed-password"))
    await prepare(main.app.state.runtime)
    assert (await client.get("/users/me")).status_code == 401
    response = await anonymous.post(
        "/auth/signin", json={"login": "root", "password": "changed-password"}
    )
    assert response.status_code == 204


async def test_storage_stats_count_only_readable_files(client, storage, make_user):
    await upload(client, path(storage, "open/a.pdf"), PDF)
    await upload(client, path(storage, "open/deep/b.pdf"), PDF + b"x")
    await upload(client, path(storage, "flat/c.pdf"), PDF + b"xx")
    await upload(client, path(storage, "flat/inner/d.pdf"), PDF)
    await upload(client, path(storage, "closed/e.pdf"), PDF)
    user = await make_user(
        "counter",
        "user",
        {"path": path(storage, "open")},
        {"path": path(storage, "open/deep")},
        {"path": path(storage, "flat"), "recursive": False},
        {"path": path(storage, "closed"), "can_read": False, "can_upload": True},
    )
    seen = (await user.get(f"/storages/{storage['id']}")).json()
    assert seen["files"] == 3
    assert seen["total_size"] == 3 * len(PDF) + 3
    full = (await client.get(f"/storages/{storage['id']}")).json()
    assert full["files"] == 5
    other = (await client.post("/storages", json={"name": "other"})).json()
    await upload(client, path(other, "x.pdf"), PDF)
    listed = {item["id"]: item for item in (await user.get("/storages")).json()}
    assert list(listed) == [storage["id"]]
    assert (listed[storage["id"]]["files"], listed[storage["id"]]["total_size"]) == (
        3,
        3 * len(PDF) + 3,
    )
    everything = {item["id"]: item for item in (await client.get("/storages")).json()}
    assert everything[storage["id"]]["files"] == 5
    assert (
        everything[other["id"]]["files"],
        everything[other["id"]]["total_size"],
    ) == (
        1,
        len(PDF),
    )


async def test_signin_is_rate_limited_by_ip(anonymous, monkeypatch):
    monkeypatch.setattr(settings.rate_limit.SIGNIN_IP, "limit", 3)
    for index in range(3):
        await anonymous.post(
            "/auth/signin", json={"login": f"nobody{index}", "password": "wrong-pass"}
        )
    response = await anonymous.post(
        "/auth/signin", json={"login": "root", "password": "root-password"}
    )
    assert response.status_code == 429 and code(response) == "rate_limited"
    assert 0 < int(response.headers["retry-after"]) <= 60
    assert response.json()["error"]["retry_after"] == int(
        response.headers["retry-after"]
    )


async def test_signin_is_rate_limited_by_login(anonymous, monkeypatch):
    monkeypatch.setattr(settings.rate_limit.SIGNIN_LOGIN, "limit", 2)
    for _ in range(2):
        await anonymous.post(
            "/auth/signin", json={"login": "Root", "password": "wrong-pass"}
        )
    response = await anonymous.post(
        "/auth/signin", json={"login": "root", "password": "root-password"}
    )
    assert response.status_code == 429 and code(response) == "rate_limited"
    assert 0 < int(response.headers["retry-after"]) <= 900


async def test_rate_limit_disabled(anonymous, monkeypatch):
    monkeypatch.setattr(settings.rate_limit, "ENABLED", False)
    monkeypatch.setattr(settings.rate_limit.SIGNIN_IP, "limit", 1)
    for _ in range(3):
        response = await anonymous.post(
            "/auth/signin", json={"login": "nobody", "password": "wrong-pass"}
        )
        assert response.status_code == 401


async def test_security_headers(client):
    response = await client.get("/users/me")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["cache-control"] == "no-store"
    docs = await client.get("http://test/docs")
    assert docs.status_code == 200 and "content-security-policy" not in docs.headers


async def test_cross_site_writes_are_rejected(client):
    response = await client.post(
        "/auth/logout/all", headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403 and code(response) == "cross_site_request"
    response = await client.post("/auth/logout/all", headers={"Origin": "http://test"})
    assert response.status_code == 204


async def test_cors_allows_configured_frontend(client):
    preflight = await client.options(
        "/auth/signin",
        headers={
            "Origin": "http://front.test",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://front.test"
    assert preflight.headers["access-control-allow-credentials"] == "true"
    response = await client.post(
        "/auth/logout/all", headers={"Origin": "http://front.test"}
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "http://front.test"
    assert "Retry-After" in response.headers["access-control-expose-headers"]
    foreign = await client.options(
        "/auth/signin",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in foreign.headers


async def test_first_request_after_postgres_restart_succeeds(client, engine):
    assert (await client.get("/account")).status_code == 200
    async with engine.connect() as admin:
        await admin.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        )
    assert (await client.get("/account")).status_code == 200
    assert (await client.get("/storages")).status_code == 200
