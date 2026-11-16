import asyncio
import json
import os

from src.application.tasks import upload as upload_tasks
from src.infrastructure.background import WorkerRuntime, app
from src.infrastructure.database.repositories import FileRepository
from tests.fakes import FakeScanner, FakeTelegram

app.conf.broker_url = os.environ["WORKER_BROKER_URL"]
RECORD = os.environ["WORKER_RECORD"]


def record(**event) -> None:
    with open(RECORD, "a") as file:
        file.write(json.dumps({"pid": os.getpid(), **event}) + "\n")


class RecordingTelegram(FakeTelegram):
    def __init__(self) -> None:
        super().__init__()
        self.next_message = os.getpid() * 100_000
        self.delay = float(os.environ.get("WORKER_TELEGRAM_DELAY", "0"))

    async def send(self, chat_id, *args, **kwargs):
        self.chats.setdefault(chat_id, {}).setdefault("messages", {})
        record(event="start", name=kwargs["name"])
        sent = await super().send(chat_id, *args, **kwargs)
        record(event="send", name=kwargs["name"], message_id=sent.message_id)
        return sent

    async def delete(self, chat_id, message_ids):
        record(event="delete", message_ids=message_ids)


if os.environ.get("WORKER_HOLD_COMMIT"):
    create = FileRepository.create

    async def hold(self, file):
        record(event="hold", name=file.name)
        await asyncio.sleep(3600)
        return await create(self, file)

    FileRepository.create = hold

telegram = RecordingTelegram()
upload_tasks.runtime = WorkerRuntime(telegram=lambda: telegram, scanner=FakeScanner)

__all__ = ["app"]
