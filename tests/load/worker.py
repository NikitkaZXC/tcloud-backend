import os

from src.application.tasks import upload as upload_tasks
from src.infrastructure.background import WorkerRuntime, app
from tests.fakes import FakeScanner, FakeTelegram


class LoadTelegram(FakeTelegram):
    async def send(self, chat_id, *args, **kwargs):
        self.chats.setdefault(
            chat_id,
            {"title": "", "about": "", "photo": None, "public": False, "admin": True},
        ).setdefault("messages", {})
        return await super().send(chat_id, *args, **kwargs)


telegram = LoadTelegram()
telegram.delay = float(os.environ.get("LOAD_TELEGRAM_DELAY", "0.2"))
upload_tasks.runtime = WorkerRuntime(telegram=lambda: telegram, scanner=FakeScanner)

__all__ = ["app"]
