import pytest

from src.infrastructure.integrations import TelegramUnavailableError
from src.infrastructure.integrations.telegram import client as telegram


class FlakyTelethon:
    def __init__(self, failures: int) -> None:
        self.up = True
        self.failures = failures
        self.connects = 0
        self.session = type("Session", (), {"filename": "test.session"})()

    def is_connected(self) -> bool:
        return self.up

    async def connect(self) -> None:
        self.connects += 1
        if self.failures:
            self.failures -= 1
            raise ConnectionError("network is down")
        self.up = True

    async def get_me(self):
        if not self.up:
            raise ConnectionError("Cannot send requests while disconnected")
        return type("Me", (), {"premium": True})()

    async def disconnect(self) -> None:
        self.up = False


def make(fake: FlakyTelethon) -> telegram.TelegramClient:
    client = telegram.TelegramClient.__new__(telegram.TelegramClient)
    client._client = fake
    client._wanted = True
    return client


async def test_dropped_client_reconnects_on_next_use():
    fake = FlakyTelethon(failures=0)
    client = make(fake)
    assert await client.is_premium()
    fake.up = False
    assert await client.is_premium()
    assert fake.connects == 1 and client.connected()


async def test_reconnect_backs_off_then_recovers(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(telegram.time, "monotonic", lambda: now[0])
    fake = FlakyTelethon(failures=2)
    fake.up = False
    client = make(fake)
    with pytest.raises(TelegramUnavailableError):
        await client.is_premium()
    assert fake.connects == 1 and client._backoff == telegram.RECONNECT_MIN
    with pytest.raises(TelegramUnavailableError, match="next reconnect"):
        await client.is_premium()
    assert fake.connects == 1
    now[0] += telegram.RECONNECT_MIN
    with pytest.raises(TelegramUnavailableError):
        await client.is_premium()
    assert fake.connects == 2 and client._backoff == 2 * telegram.RECONNECT_MIN
    now[0] += 2 * telegram.RECONNECT_MIN
    assert await client.is_premium()
    assert fake.connects == 3 and client._backoff == 0


async def test_backoff_is_capped(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(telegram.time, "monotonic", lambda: now[0])
    fake = FlakyTelethon(failures=100)
    fake.up = False
    client = make(fake)
    for _ in range(20):
        now[0] += telegram.RECONNECT_MAX
        with pytest.raises(TelegramUnavailableError):
            await client.ensure_connected()
    assert client._backoff == telegram.RECONNECT_MAX


async def test_stopped_client_is_not_reconnected():
    fake = FlakyTelethon(failures=0)
    client = make(fake)
    await client.stop()
    with pytest.raises(TelegramUnavailableError):
        await client.is_premium()
    assert fake.connects == 0


class PingTelethon(FlakyTelethon):
    def __init__(self) -> None:
        super().__init__(failures=0)
        self.calls = 0

    async def __call__(self, request):
        self.calls += 1
        if not self.up:
            raise ConnectionError("down")
        return None


async def test_ping_is_a_cached_round_trip(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(telegram.time, "monotonic", lambda: now[0])
    fake = PingTelethon()
    client = make(fake)
    client.authorized = True
    assert await client.ping() and fake.calls == 1
    assert await client.ping() and fake.calls == 1
    now[0] += telegram.PING_TTL
    fake.up, fake.failures = False, 1
    assert not await client.ping()
    client.authorized = False
    now[0] += telegram.PING_TTL
    assert not await client.ping()
