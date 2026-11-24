import asyncio
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from celery.contrib.testing.worker import start_worker
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

import main
from src.application.tasks import upload as upload_tasks
from src.config import settings
from src.infrastructure.background import WorkerRuntime, app
from src.infrastructure.enums import TelegramSession, UploadQueue
from src.infrastructure.integrations.telegram.factory import session_path
from src.infrastructure.integrations.telegram.login import session_file
from src.infrastructure.integrations.web import client as web
from tests.fakes import FakeLogin, FakeScanner, FakeTelegram

ROOT_LOGIN = "root"
ROOT_PASSWORD = "root-password"
TELEGRAM_CONCURRENCY = 2


@pytest.fixture(scope="session")
def celery_workers(redis_server: None) -> Iterator[None]:
    app.conf.broker_url = "memory://"
    app.conf.broker_transport_options = {
        **app.conf.broker_transport_options,
        "polling_interval": 0.01,
    }
    with ExitStack() as stack:
        for queues, concurrency in (
            ([UploadQueue.DOWNLOAD], 4),
            ([UploadQueue.PREPARE], 4),
            ([UploadQueue.TELEGRAM, UploadQueue.ARCHIVE], TELEGRAM_CONCURRENCY),
        ):
            names = [queue.value for queue in queues]
            stack.enter_context(
                start_worker(
                    app,
                    concurrency=concurrency,
                    pool="threads",
                    perform_ping_check=False,
                    queues=names,
                    hostname=f"{names[0]}@test",
                )
            )
            upload_tasks.heartbeat.start(names, f"{names[0]}@test")
        yield
        upload_tasks.heartbeat.stop()


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
def scanner() -> FakeScanner:
    return FakeScanner()


@pytest.fixture
def upload_session() -> Iterator[Path]:
    path = session_file(session_path(TelegramSession.UPLOAD))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"session")
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def upload_telegram(upload_session: Path) -> FakeTelegram:
    return FakeTelegram(session_path(TelegramSession.UPLOAD))


@pytest.fixture
def download_session() -> Iterator[Path]:
    path = session_file(session_path(TelegramSession.DOWNLOAD))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"session")
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def download_telegram(download_session: Path, telegram: FakeTelegram) -> FakeTelegram:
    fake = FakeTelegram(session_path(TelegramSession.DOWNLOAD))
    fake.chats = telegram.chats
    return fake


@pytest.fixture
def logins() -> dict[TelegramSession, FakeLogin]:
    return {}


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
    upload_telegram: FakeTelegram,
    download_telegram: FakeTelegram,
    logins: dict[TelegramSession, FakeLogin],
    scanner: FakeScanner,
    tmp_path,
    monkeypatch,
) -> AsyncIterator[Callable[[], Awaitable[AsyncClient]]]:
    def create_telegram(session=TelegramSession.API, receive_updates=True):
        return {
            TelegramSession.API: telegram,
            TelegramSession.UPLOAD: upload_telegram,
            TelegramSession.DOWNLOAD: download_telegram,
        }[session]

    def create_login(session: TelegramSession) -> FakeLogin:
        logins[session] = FakeLogin(session_path(session))
        return logins[session]

    monkeypatch.setattr(settings.upload, "TMP_DIR", tmp_path / "tmp")
    monkeypatch.setattr(main, "create_telegram", create_telegram)
    monkeypatch.setattr(main, "create_login", create_login)
    monkeypatch.setattr(main, "create_scanner", lambda: scanner)
    opened = []

    async def make() -> AsyncClient:
        lifespan = main.app.router.lifespan_context(main.app)
        await lifespan.__aenter__()
        client = AsyncClient(
            transport=ASGITransport(main.app), base_url="http://test/api"
        )
        opened.append((lifespan, client))
        await sign_in(client, ROOT_LOGIN, ROOT_PASSWORD)
        return client

    yield make
    for lifespan, client in reversed(opened):
        await settle(main.app.state.runtime.store)
        await asyncio.to_thread(worker.run, worker.drain())
        await client.aclose()
        await lifespan.__aexit__(None, None, None)


async def sign_in(client: AsyncClient, login: str, password: str) -> None:
    response = await client.post(
        "/auth/signin", json={"login": login, "password": password}
    )
    assert response.status_code == 204, response.text


async def settle(store, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while await store.count_active() and time.monotonic() < deadline:
        await asyncio.sleep(0.05)


@pytest.fixture
async def client(make_client) -> AsyncClient:
    return await make_client()


@pytest.fixture
async def login_as(client: AsyncClient) -> AsyncIterator[Callable]:
    opened = []

    async def make(login: str, password: str) -> AsyncClient:
        other = AsyncClient(
            transport=ASGITransport(main.app), base_url="http://test/api"
        )
        opened.append(other)
        await sign_in(other, login, password)
        return other

    yield make
    for other in opened:
        await other.aclose()


@pytest.fixture
async def make_user(client: AsyncClient, login_as) -> Callable:
    async def make(login: str, role: str = "user", *grants: dict) -> AsyncClient:
        password = f"{login}-password"
        created = await client.post(
            "/users", json={"login": login, "password": password, "role": role}
        )
        assert created.status_code == 201, created.text
        for grant in grants:
            response = await client.put(
                f"/users/{created.json()['id']}/grants", json=grant
            )
            assert response.status_code == 200, response.text
        other = await login_as(login, password)
        other.user_id = created.json()["id"]
        return other

    return make


@pytest.fixture
async def storage(client: AsyncClient) -> dict:
    response = await client.post(
        "/storages", json={"name": "Фото", "description": "мои фото"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def split(data: dict) -> dict:
    return {
        **data,
        "folders": [item for item in data["items"] if item["type"] == "folder"],
        "files": [item for item in data["items"] if item["type"] == "file"],
    }


async def listing(client: AsyncClient, target: str, **params) -> dict:
    response = await client.get(
        "/folders", params={"path": target, "limit": 200, **params}
    )
    assert response.status_code == 200, response.text
    return split(response.json())


def path(storage: dict, rest: str = "") -> str:
    return f"tcloud/{storage['id']}" + (f"/{rest}" if rest else "")


async def wait_task(client: AsyncClient, task_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    task = {}
    while time.monotonic() < deadline:
        task = (await client.get(f"/tasks/{task_id}")).json()
        if task["status"] in ("done", "failed", "cancelled"):
            return task
        await asyncio.sleep(0.02)
    raise AssertionError(f"task {task_id} did not finish: {task}")


async def build_archive(
    client: AsyncClient, target: str, **params
) -> tuple[dict, object]:
    response = await client.post("/folders/archive", params={"path": target, **params})
    assert response.status_code == 202, response.text
    task = await wait_task(client, response.json()["id"])
    return task, await client.get(f"/tasks/{task['id']}/archive")


async def start_upload(client: AsyncClient, target: str, data: bytes, **options):
    response = await client.post(
        "/uploads", params={"path": target, **options}, content=data
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


async def upload(client: AsyncClient, target: str, data: bytes, **options) -> dict:
    return await wait_task(client, await start_upload(client, target, data, **options))


class Remote(BaseHTTPRequestHandler):
    files: dict = {}
    hooks: list = []

    def do_GET(self):
        chunked = self.path.startswith("/chunked/")
        body = self.files.get(self.path.removeprefix("/chunked"))
        self.send_response(200 if body is not None else 404)
        if not chunked:
            self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.hooks.append((dict(self.headers), body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(web, "ip_allowed", lambda ip: True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Remote)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    Remote.files.clear()
    Remote.hooks.clear()
