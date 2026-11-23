from src.infrastructure.cache import MediaCache
from src.infrastructure.cache import media as module


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
