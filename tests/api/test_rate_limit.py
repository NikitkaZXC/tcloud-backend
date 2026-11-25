import uuid

from httpx import ASGITransport, AsyncClient

import main
from src.config import settings
from tests.api.conftest import Remote, path, wait_task
from tests.factories import make_image


def code(response) -> str:
    return response.json()["error"]["code"]


def limited(response) -> bool:
    return response.status_code == 429 and code(response) == "rate_limited"


async def test_general_rule_counts_cookie_and_api_key_per_user(
    client, make_user, monkeypatch
):
    user = await make_user("scripted")
    created = await client.post(f"/users/{user.user_id}/api-keys", json={"name": "k"})
    monkeypatch.setattr(settings.rate_limit.API_USER, "limit", 2)
    assert (await user.get("/account/settings")).status_code == 200
    async with AsyncClient(
        transport=ASGITransport(main.app),
        base_url="http://test/api",
        headers={"X-API-Key": created.json()["key"]},
    ) as keyed:
        assert (await keyed.get("/account/settings")).status_code == 200
        response = await keyed.get("/storages")
    assert limited(response)
    assert 0 < int(response.headers["retry-after"]) <= 60


async def test_upload_rule_rejects_before_reading_body(client, storage, monkeypatch):
    monkeypatch.setattr(settings.rate_limit.UPLOAD_USER, "limit", 1)
    params = {"path": path(storage, "a.jpg"), "encrypt": "false"}
    first = await client.post("/uploads", params=params, content=make_image())
    assert first.status_code == 202
    params["path"] = path(storage, "b.jpg")
    second = await client.post("/uploads", params=params, content=make_image())
    assert limited(second) and int(second.headers["retry-after"]) > 0
    url = await client.post(
        "/uploads/from-url",
        json={"url": "https://example.com/a.pdf", "path": path(storage, "c.pdf")},
    )
    assert limited(url)


async def test_file_reads_skip_general_rule(client, monkeypatch):
    monkeypatch.setattr(settings.rate_limit.API_USER, "limit", 1)
    monkeypatch.setattr(settings.rate_limit.FILE_READ_USER, "limit", 4)
    missing = uuid.uuid4()
    urls = [
        f"/files/{missing}/thumb",
        f"/files/{missing}/download",
        f"/tasks/{missing}/archive",
        f"/files/{missing}/thumb",
    ]
    for url in urls:
        assert (await client.get(url)).status_code == 404
    assert limited(await client.get(f"/files/{missing}/thumb"))
    assert (await client.get("/account/settings")).status_code == 200
    assert limited(await client.get("/account/settings"))


async def test_health_is_limited_by_ip(client, monkeypatch):
    monkeypatch.setattr(settings.rate_limit.HEALTH_IP, "limit", 1)
    assert (await client.get("/health")).status_code in (200, 503)
    assert limited(await client.get("/health"))


async def test_telegram_login_rule(client, monkeypatch):
    monkeypatch.setattr(settings.rate_limit.TELEGRAM_LOGIN_USER, "limit", 1)
    await client.post("/telegram/api/code", json={"phone": "+10000000000"})
    assert limited(await client.post("/telegram/api/code", json={"phone": "+1"}))


async def test_disabled_flag_turns_every_rule_off(client, monkeypatch):
    monkeypatch.setattr(settings.rate_limit, "ENABLED", False)
    monkeypatch.setattr(settings.rate_limit.API_USER, "limit", 1)
    for _ in range(3):
        assert (await client.get("/account/settings")).status_code == 200


async def test_telegram_sends_are_limited_per_user_not_for_root(
    client, storage, make_user, monkeypatch
):
    monkeypatch.setattr(settings.rate_limit.TELEGRAM_SEND_MINUTE_USER, "limit", 2)
    user = await make_user(
        "sender", "user", {"path": path(storage, "in"), "can_upload": True}
    )
    for index in range(2):
        response = await user.post(
            "/uploads",
            params={"path": path(storage, f"in/{index}.jpg"), "encrypt": "false"},
            content=make_image(),
        )
        assert response.status_code == 202, response.text
        await wait_task(user, response.json()["id"])
    response = await user.post(
        "/uploads/sessions", params={"path": path(storage, "in/x.bin"), "size": 5}
    )
    error = response.json()["error"]
    assert response.status_code == 429 and error["code"] == "send_quota_exceeded"
    assert error["limit"] == 2 and error["window"] == 60
    assert 0 < int(response.headers["retry-after"]) <= 60
    skipped = await user.post(
        "/uploads",
        params={"path": path(storage, "in/0.jpg"), "on_conflict": "skip"},
        content=b"x",
    )
    assert skipped.status_code == 202 and skipped.json()["status"] == "done"
    for index in range(3):
        response = await client.post(
            "/uploads",
            params={"path": path(storage, f"root/{index}.jpg"), "encrypt": "false"},
            content=make_image(),
        )
        assert response.status_code == 202, response.text


async def test_daily_send_quota(client, storage, make_user, monkeypatch):
    monkeypatch.setattr(settings.rate_limit.TELEGRAM_SEND_DAY_USER, "limit", 1)
    user = await make_user("daily", "admin")
    body = {"url": "https://example.com/a.pdf", "path": path(storage, "a.pdf")}
    assert (await user.post("/uploads/from-url", json=body)).status_code == 202
    body["path"] = path(storage, "b.pdf")
    response = await user.post("/uploads/from-url", json=body)
    assert code(response) == "send_quota_exceeded"
    assert response.json()["error"]["window"] == 86400


async def test_storage_create_is_limited(client, monkeypatch):
    monkeypatch.setattr(settings.rate_limit.STORAGE_CREATE_USER, "limit", 1)
    first = await client.post("/storages", json={"name": "one"})
    assert first.status_code == 201
    assert limited(await client.post("/storages", json={"name": "two"}))


async def sends(user, scope: str) -> int:
    count, _ = await main.app.state.runtime.rate_limits.hit(
        f"{scope}:user:{user.user_id}", 10_000, 86400, False
    )
    return count - 1


async def test_batch_over_minute_limit_records_only_accepted(
    client, storage, make_user, remote, monkeypatch
):
    monkeypatch.setattr(settings.rate_limit.TELEGRAM_SEND_MINUTE_USER, "limit", 2)
    user = await make_user("batcher", "admin")
    Remote.files["/a.jpg"] = make_image()
    items = [
        {"url": f"{remote}/a.jpg", "path": path(storage, f"batch/{index}.jpg")}
        for index in range(4)
    ]
    body = (await user.post("/uploads/batch", json={"items": items})).json()
    errors = [item["error"] and item["error"]["code"] for item in body["items"]]
    assert errors == [None, None, "send_quota_exceeded", "send_quota_exceeded"]
    assert await sends(user, "telegram_send_day") == 2
    assert await sends(user, "telegram_send_minute") == 2
    for item in body["items"][:2]:
        await wait_task(user, item["task"]["id"])


async def test_rejected_uploads_record_no_send(
    client, storage, make_user, telegram, monkeypatch
):
    user = await make_user(
        "rejected", "user", {"path": path(storage, "r"), "can_upload": True}
    )
    telegram.delay = 1
    params = {"path": path(storage, "r/a.bin"), "encrypt": "false"}
    first = await user.post("/uploads", params=params, content=b"x" * 100)
    assert first.status_code == 202
    busy = await user.post("/uploads", params=params, content=b"x" * 100)
    assert code(busy) == "upload_in_progress"
    monkeypatch.setattr(settings.upload, "USER_DISK_QUOTA_MB", 1)
    big = await user.post(
        "/uploads",
        params={"path": path(storage, "r/b.bin"), "encrypt": "false"},
        content=b"x" * (2 * 1024 * 1024),
    )
    assert code(big) == "temp_quota_exceeded"
    assert await sends(user, "telegram_send_minute") == 1
    assert await sends(user, "telegram_send_day") == 1
    telegram.delay = 0
    await wait_task(user, first.json()["id"])
