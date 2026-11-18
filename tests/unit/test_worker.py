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
