from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.api.dependencies import (
    RateLimiter,
    client_ip,
    rate_limit_by_ip,
    rate_limit_by_user,
)
from src.application.exception import RateLimitedError
from src.config import settings
from src.config.models import RateLimitRule

RULE = RateLimitRule(limit=3, window=60)


def request(host: str | None):
    return SimpleNamespace(client=SimpleNamespace(host=host) if host else None)


async def test_under_limit_passes():
    store = AsyncMock()
    store.hit.return_value = (3, 50)
    await RateLimiter(store).hit("signin", "ip:1.1.1.1", RULE)
    store.hit.assert_awaited_once_with("signin:ip:1.1.1.1", 3, 60)


async def test_over_limit_raises_with_retry_after():
    store = AsyncMock()
    store.hit.return_value = (4, 17)
    with pytest.raises(RateLimitedError) as error:
        await RateLimiter(store).hit("signin", "ip:1.1.1.1", RULE)
    assert error.value.to_dict()["code"] == "rate_limited"
    assert error.value.extra == {"retry_after": 17}


async def test_retry_after_is_at_least_one():
    store = AsyncMock()
    store.hit.return_value = (4, -1)
    with pytest.raises(RateLimitedError) as error:
        await RateLimiter(store).hit("signin", "x", RULE)
    assert error.value.extra == {"retry_after": 1}


async def test_redis_error_fails_open():
    store = AsyncMock()
    store.hit.side_effect = ConnectionError
    await RateLimiter(store).hit("signin", "x", RULE)


async def test_disabled(monkeypatch):
    monkeypatch.setattr(settings.rate_limit, "ENABLED", False)
    store = AsyncMock()
    await RateLimiter(store).hit("signin", "x", RULE)
    store.hit.assert_not_awaited()


async def test_by_ip_dependency():
    limiter = AsyncMock()
    await rate_limit_by_ip("signin", RULE)(request("8.8.8.8"), limiter)
    limiter.hit.assert_awaited_once_with("signin", "ip:8.8.8.8", RULE)


async def test_by_user_dependency():
    limiter = AsyncMock()
    actor = SimpleNamespace(user_id=uuid4())
    await rate_limit_by_user("upload", RULE)(actor, limiter)
    limiter.hit.assert_awaited_once_with("upload", f"user:{actor.user_id}", RULE)


def test_client_ip():
    assert client_ip(request("10.0.0.1")) == "10.0.0.1"
    assert client_ip(request(None)) == "unknown"


def test_rules_from_env(monkeypatch):
    from src.config.models import RateLimitSettings

    monkeypatch.setenv("RATE_LIMIT_API_USER__LIMIT", "7")
    monkeypatch.setenv("RATE_LIMIT_API_USER__WINDOW", "30")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    loaded = RateLimitSettings()
    assert (loaded.API_USER.limit, loaded.API_USER.window) == (7, 30)
    assert loaded.ENABLED is False
