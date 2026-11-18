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
