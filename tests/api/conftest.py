import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

import main
from src.config import settings
from tests.fakes import FakeScanner, FakeTelegram

KEY = "test"


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def scanner() -> FakeScanner:
    return FakeScanner()


@pytest.fixture
async def make_client(
    engine: AsyncEngine,
    telegram: FakeTelegram,
    scanner: FakeScanner,
    tmp_path,
    monkeypatch,
) -> AsyncIterator[Callable[[], Awaitable[AsyncClient]]]:
    monkeypatch.setattr(settings.upload, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(main, "create_telegram", lambda: telegram)
    monkeypatch.setattr(main, "create_scanner", lambda: scanner)
    opened = []

    async def make() -> AsyncClient:
        lifespan = main.app.router.lifespan_context(main.app)
        await lifespan.__aenter__()
        client = AsyncClient(
            transport=ASGITransport(main.app),
            base_url="http://test/api",
            headers={"X-API-Key": KEY},
        )
        opened.append((lifespan, client, main.app.state.runtime))
        return client

    yield make
    for lifespan, client, runtime in reversed(opened):
        await runtime.drain()
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


@pytest.fixture
async def client(make_client) -> AsyncClient:
    return await make_client()


@pytest.fixture
async def storage(client: AsyncClient) -> dict:
    response = await client.post(
        "/storages", json={"name": "Фото", "description": "мои фото"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def path(storage: dict, rest: str = "") -> str:
    return f"tcloud/{storage['id']}" + (f"/{rest}" if rest else "")


async def wait_task(client: AsyncClient, task_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    task = {}
    while time.monotonic() < deadline:
        task = (await client.get(f"/tasks/{task_id}")).json()
        if task["status"] in ("done", "failed"):
            return task
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} did not finish: {task}")


async def start_upload(client: AsyncClient, target: str, data: bytes, **options) -> str:
    response = await client.post(
        "/uploads", json={"path": target, "size": len(data), **options}
    )
    assert response.status_code == 201, response.text
    task = response.json()
    if task["status"] != "done":
        await client.put(f"/uploads/{task['id']}", content=data)
    return task["id"]


async def upload(client: AsyncClient, target: str, data: bytes, **options) -> dict:
    return await wait_task(client, await start_upload(client, target, data, **options))
