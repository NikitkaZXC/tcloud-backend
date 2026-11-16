import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import ExitStack

import pytest
from celery.contrib.testing.worker import start_worker
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

import main
from src.application.tasks import upload as upload_tasks
from src.config import settings
from src.infrastructure.background import WorkerRuntime, app
from src.infrastructure.enums import UploadQueue
from tests.fakes import FakeScanner, FakeTelegram

KEY = "test"
TELEGRAM_CONCURRENCY = 2


@pytest.fixture(scope="session")
def celery_workers() -> Iterator[None]:
    app.conf.broker_url = "memory://"
    app.conf.broker_transport_options = {"polling_interval": 0.01}
    with ExitStack() as stack:
        for queue, concurrency in (
            (UploadQueue.PREPARE, 4),
            (UploadQueue.TELEGRAM, TELEGRAM_CONCURRENCY),
        ):
            stack.enter_context(
                start_worker(
                    app,
                    concurrency=concurrency,
                    pool="threads",
                    perform_ping_check=False,
                    queues=[queue.value],
                    hostname=f"{queue.value}@test",
                )
            )
        yield


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def scanner() -> FakeScanner:
    return FakeScanner()


@pytest.fixture
def worker(
    celery_workers: None, telegram: FakeTelegram, scanner: FakeScanner, monkeypatch
) -> Iterator[WorkerRuntime]:
    runtime = WorkerRuntime(telegram=lambda: telegram, scanner=lambda: scanner)
    monkeypatch.setattr(upload_tasks, "runtime", runtime)
    yield runtime
    runtime.shutdown()


@pytest.fixture
async def make_client(
    engine: AsyncEngine,
    redis,
    worker: WorkerRuntime,
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
        opened.append((lifespan, client))
        return client

    yield make
    for lifespan, client in reversed(opened):
        await settle(main.app.state.runtime.store)
        await asyncio.to_thread(worker.run, worker.drain())
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


async def settle(store, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while await store.count_active() and time.monotonic() < deadline:
        await asyncio.sleep(0.05)


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


async def start_upload(client: AsyncClient, target: str, data: bytes, **options):
    response = await client.post(
        "/uploads", params={"path": target, **options}, content=data
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def upload(client: AsyncClient, target: str, data: bytes, **options) -> dict:
    return await wait_task(client, await start_upload(client, target, data, **options))
