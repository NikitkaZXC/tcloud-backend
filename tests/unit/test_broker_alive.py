from src.infrastructure.background.celery import queue


async def test_broker_check_is_cached_for_five_seconds(monkeypatch):
    calls = []
    now = [100.0]

    async def sizes():
        calls.append(now[0])
        return {"uploads.download": 0}

    monkeypatch.setattr(queue, "_alive", None)
    monkeypatch.setattr(queue, "queue_sizes", sizes)
    monkeypatch.setattr(queue.time, "monotonic", lambda: now[0])
    assert await queue.broker_alive() and await queue.broker_alive()
    assert calls == [100.0]
    now[0] += 5
    assert await queue.broker_alive() and calls == [100.0, 105.0]
