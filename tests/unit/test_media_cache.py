import asyncio

from src.infrastructure.cache import MediaCache
from src.infrastructure.cache import media as module
from src.infrastructure.integrations import (
    TelegramMessageGoneError,
    TelegramUnavailableError,
)


def test_entries_expire_and_least_recent_is_dropped(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    cache = MediaCache(ttl=90, limit=2)
    cache.put(-1, 1, "a", 10)
    cache.put(-1, 2, "b", 20)
    assert cache.get(-1, 1) == ("a", 10)
    cache.put(-1, 3, "c", 30)
    assert cache.get(-1, 2) is None
    assert cache.get(-1, 1) == ("a", 10) and cache.get(-1, 3) == ("c", 30)
    cache.forget(-1, [3, 99])
    assert cache.get(-1, 3) is None
    now[0] = 191.0
    assert cache.get(-1, 1) is None


class Batches:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls = []
        self.fail = fail

    async def messages_media(self, chat_id, message_ids):
        self.calls.append((chat_id, sorted(message_ids)))
        if self.fail:
            raise self.fail
        return {
            message_id: (f"m{message_id}", message_id)
            for message_id in message_ids
            if message_id != 404
        }


async def test_concurrent_lookups_of_one_chat_share_one_request():
    cache, telegram = MediaCache(), Batches()
    results = await asyncio.gather(
        *(cache.load(telegram, -1, message_id) for message_id in (1, 2, 2, 3)),
        cache.load(telegram, -2, 7),
        cache.load(telegram, -1, 404),
        return_exceptions=True,
    )
    assert results[:5] == [("m1", 1), ("m2", 2), ("m2", 2), ("m3", 3), ("m7", 7)]
    assert isinstance(results[5], TelegramMessageGoneError)
    assert sorted(telegram.calls) == [(-2, [7]), (-1, [1, 2, 3, 404])]
    assert await cache.load(telegram, -1, 2) == ("m2", 2) and len(telegram.calls) == 2
    await cache.load(telegram, -1, 5)
    assert telegram.calls[-1] == (-1, [5])


async def test_failed_batch_fails_every_waiter_and_is_not_cached():
    cache = MediaCache()
    telegram = Batches(TelegramUnavailableError("down"))
    results = await asyncio.gather(
        cache.load(telegram, -1, 1), cache.load(telegram, -1, 2), return_exceptions=True
    )
    assert all(isinstance(item, TelegramUnavailableError) for item in results)
    assert cache.get(-1, 1) is None and len(telegram.calls) == 1


async def test_cancelled_waiter_does_not_break_the_batch():
    cache, telegram = MediaCache(), Batches()
    first = asyncio.create_task(cache.load(telegram, -1, 1))
    second = asyncio.create_task(cache.load(telegram, -1, 2))
    await asyncio.sleep(0)
    first.cancel()
    assert await second == ("m2", 2)
    assert cache.get(-1, 1) == ("m1", 1)
