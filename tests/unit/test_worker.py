import asyncio

import pytest

from src.infrastructure.background import WorkerRuntime
from src.infrastructure.integrations.telegram import TelegramUnauthorizedError
from tests.fakes import FakeTelegram


async def test_unauthorized_worker_points_to_upload_login():
    telegram = FakeTelegram()
    telegram.authorized = False
    runtime = WorkerRuntime(lambda: telegram, lambda: None)
    with pytest.raises(TelegramUnauthorizedError, match="/api/telegram/upload$"):
        await runtime.telegram()


def test_drain_returns_when_tracked_tasks_are_done_but_not_yet_discarded():
    runtime = WorkerRuntime(lambda: None, lambda: None)
    loop = runtime._start()

    async def finished_before_discard():
        task = runtime.spawn(asyncio.sleep(3600))
        task.cancel()
        await asyncio.sleep(0)
        assert task.done() and task in runtime._tasks
        await runtime.drain()

    try:
        asyncio.run_coroutine_threadsafe(finished_before_discard(), loop).result(5)
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_worker_telegram_needs_the_upload_session(monkeypatch):
    from src.application.tasks import upload as tasks

    monkeypatch.setattr(tasks, "session_exists", lambda session: False)
    with pytest.raises(TelegramUnauthorizedError, match="/api/telegram/upload"):
        tasks._telegram()
    made = []
    monkeypatch.setattr(tasks, "session_exists", lambda session: True)
    monkeypatch.setattr(
        tasks,
        "create_telegram",
        lambda session, receive_updates: made.append((session, receive_updates)),
    )
    tasks._telegram()
    assert made == [(tasks.TelegramSession.UPLOAD, False)]


def test_worker_signals_start_and_stop_the_heartbeat(monkeypatch):
    from types import SimpleNamespace

    from src.application.tasks import upload as tasks

    events = []
    beat = SimpleNamespace(
        start=lambda queues, host, state: events.append(("start", queues, host)),
        stop=lambda: events.append(("stop",)),
    )
    monkeypatch.setattr(tasks, "heartbeat", beat)
    monkeypatch.setattr(
        tasks, "runtime", SimpleNamespace(shutdown=lambda: events.append(("down",)))
    )
    sender = SimpleNamespace(
        hostname="w@h",
        task_consumer=SimpleNamespace(queues=[SimpleNamespace(name="uploads.prepare")]),
    )
    tasks.on_worker_ready(sender=sender)
    tasks.on_worker_shutdown()
    assert events == [("start", ["uploads.prepare"], "w@h"), ("stop",), ("down",)]
