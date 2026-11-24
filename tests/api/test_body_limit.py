import pytest
from httpx import ASGITransport, AsyncClient

import main
from src.api.middleware.body_limit import streams_body
from src.config import settings
from tests.api.conftest import path, upload
from tests.factories import PDF


@pytest.fixture
async def anonymous(client):
    other = AsyncClient(transport=ASGITransport(main.app), base_url="http://test/api")
    yield other
    await other.aclose()


@pytest.fixture
def small_limit(monkeypatch):
    monkeypatch.setattr(settings.app, "MAX_BODY_SIZE", 1000)


def rejected(response) -> bool:
    error = response.json()["error"]
    return (
        response.status_code == 413
        and error["code"] == "body_too_large"
        and error["limit"] == 1000
    )


async def test_big_json_is_rejected_before_auth(anonymous, small_limit):
    response = await anonymous.post(
        "/auth/signin", json={"login": "x" * 5000, "password": "y"}
    )
    assert rejected(response)
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_chunked_body_is_counted(anonymous, small_limit):
    async def chunks():
        for _ in range(10):
            yield b"x" * 200

    response = await anonymous.post(
        "/auth/signin", content=chunks(), headers={"content-type": "application/json"}
    )
    assert "content-length" not in response.request.headers
    assert rejected(response)


async def test_small_body_passes(anonymous, small_limit):
    response = await anonymous.post(
        "/auth/signin", json={"login": "nobody", "password": "wrong-password"}
    )
    assert response.status_code == 401


async def test_streaming_routes_are_not_limited(client, storage, small_limit):
    task = await upload(client, path(storage, "big.pdf"), PDF + b"0" * 5000)
    assert task["status"] == "done", task
    session = await client.post(
        "/uploads/sessions", params={"path": path(storage, "s.bin"), "size": 3000}
    )
    response = await client.put(
        f"/uploads/sessions/{session.json()['session_id']}",
        params={"offset": 0},
        content=b"z" * 3000,
    )
    assert response.status_code == 202, response.text
    photo = await client.put(f"/storages/{storage['id']}/photo", content=b"x" * 3000)
    assert photo.json()["error"]["code"] != "body_too_large"


def test_only_body_streaming_routes_are_exempt():
    exempt = {
        (method.upper(), route)
        for route, methods in main.app.openapi()["paths"].items()
        for method in methods
        if streams_body(method.upper(), route.replace("{", "x").replace("}", ""))
    }
    assert exempt == {
        ("POST", "/api/uploads"),
        ("PUT", "/api/uploads/sessions/{session_id}"),
        ("PUT", "/api/storages/{storage_id}/photo"),
    }
