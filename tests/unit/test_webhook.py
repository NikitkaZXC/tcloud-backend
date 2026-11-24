import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from loguru import logger

from src.application.services import webhook
from src.infrastructure.integrations import UnsafeUrlError

URL = "https://user:pass@hook.example:8443/secret/path?token=abc"


class View(dict):
    user_id = "0190f3c2-0000-7000-8000-000000000001"


STATE = {"status": "running"}


class FakeTasks:
    def __init__(self, session, store):
        pass

    async def get(self, task_id, actor):
        return View(id=str(task_id), status=STATE["status"])


@asynccontextmanager
async def sessions():
    yield None


@pytest.fixture
def messages(monkeypatch):
    monkeypatch.setattr(webhook, "TaskService", FakeTasks)
    monkeypatch.setattr(webhook.asyncio, "sleep", lambda delay: _noop())
    found: list[str] = []
    sink = logger.add(lambda message: found.append(message.record["message"]))
    yield found
    logger.remove(sink)


async def _noop():
    pass


@pytest.mark.parametrize(
    "error",
    [
        UnsafeUrlError("url points to a private or reserved address"),
        httpx.ConnectError("refused"),
    ],
)
async def test_webhook_logs_only_scheme_and_host(monkeypatch, messages, error):
    @asynccontextmanager
    async def failing(*args, **kwargs):
        raise error
        yield

    monkeypatch.setattr(webhook, "open_url", failing)
    await webhook.send_hook(None, sessions, "0192d8a0-0000-7000-8000-000000000000", URL)
    logged = [m for m in messages if m.startswith("Webhook")]
    assert logged and all("https://hook.example " in m for m in logged)
    assert not any(
        part in m for m in logged for part in ("secret", "token", "pass", "8443")
    )


def test_webhook_key_is_hkdf_of_secret_per_user():
    import hashlib
    import hmac

    from src.config import settings
    from src.infrastructure.security import webhook_key

    secret = settings.app.SECRET_KEY.get_secret_value().encode()
    prk = hmac.new(b"\0" * 32, secret, hashlib.sha256).digest()
    info = f"tcloud-webhook:{View.user_id}".encode()
    expected = hmac.new(prk, info + b"\x01", hashlib.sha256).digest()
    assert webhook_key(View.user_id) == expected
    assert webhook_key("another") != expected


class Response:
    def __init__(self, status_code):
        self.status_code = status_code


def receiver(monkeypatch, on_send, codes=()):
    sent: list[str] = []
    flight = {"now": 0, "max": 0}
    codes = list(codes)

    @asynccontextmanager
    async def fake_open(url, method, headers, content):
        flight["now"] += 1
        flight["max"] = max(flight["max"], flight["now"])
        sent.append(json.loads(content)["status"])
        await on_send(len(sent))
        flight["now"] -= 1
        yield Response(codes.pop(0) if codes else 204)

    monkeypatch.setattr(webhook, "open_url", fake_open)
    return sent, flight


async def run_hooks(task_id, status):
    spawned: list[asyncio.Task] = []
    STATE["status"] = status
    webhook.notify(
        lambda c: spawned.append(asyncio.create_task(c)), None, sessions, task_id, URL
    )
    while not all(task.done() for task in spawned):
        await asyncio.gather(*spawned)
    return spawned


async def test_one_webhook_in_flight_and_the_last_state_wins(monkeypatch, messages):
    task_id = "0192d8a0-0000-7000-8000-000000000001"
    spawned: list[asyncio.Task] = []

    def spawn(coroutine):
        spawned.append(asyncio.create_task(coroutine))

    async def on_send(count):
        if count == 1:
            for status in ("running", "running", "done"):
                STATE["status"] = status
                webhook.notify(spawn, None, sessions, task_id, URL)
        await asyncio.sleep(0)

    sent, flight = receiver(monkeypatch, on_send)
    STATE["status"] = "running"
    webhook.notify(spawn, None, sessions, task_id, URL)
    while not all(task.done() for task in spawned):
        await asyncio.gather(*spawned)
    assert sent == ["running", "done"]
    assert len(spawned) == 1 and flight["max"] == 1
    assert task_id not in webhook.sending and task_id not in webhook.pending


async def test_retry_sends_the_fresh_state(monkeypatch, messages):
    task_id = "0192d8a0-0000-7000-8000-000000000002"

    async def on_send(count):
        STATE["status"] = "done"

    sent, _ = receiver(monkeypatch, on_send, codes=[503])
    await run_hooks(task_id, "running")
    assert sent == ["running", "done"]


async def test_failed_delivery_frees_the_slot(monkeypatch, messages):
    task_id = "0192d8a0-0000-7000-8000-000000000003"

    async def on_send(count):
        raise RuntimeError("boom")

    receiver(monkeypatch, on_send)
    await run_hooks(task_id, "running")
    assert task_id not in webhook.sending
    assert any(m.startswith("Webhook of task") for m in messages)
