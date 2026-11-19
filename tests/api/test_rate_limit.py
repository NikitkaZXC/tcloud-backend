import uuid

from httpx import ASGITransport, AsyncClient

import main
from src.config import settings
from tests.api.conftest import path
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
