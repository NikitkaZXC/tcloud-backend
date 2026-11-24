from contextlib import asynccontextmanager

import httpx
import pytest
from loguru import logger

from src.application.services import webhook
from src.infrastructure.integrations import UnsafeUrlError

URL = "https://user:pass@hook.example:8443/secret/path?token=abc"


class View(dict):
    user_id = "0190f3c2-0000-7000-8000-000000000001"


class FakeTasks:
    def __init__(self, session, store):
        pass

    async def get(self, task_id, actor):
        return View(id=str(task_id))


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
