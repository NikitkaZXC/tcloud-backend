import asyncio
from types import SimpleNamespace

from src.application.services import pipeline
from src.application.services.pipeline import UploadPipeline
from src.config import settings


class FlakyStore:
    def __init__(self):
        self.touches = 0
        self.checks = 0

    async def touch(self, task_id):
        self.touches += 1
        if self.touches == 1:
            raise ConnectionError("redis blip")

    async def cancel_requested(self, task_id):
        self.checks += 1
        if self.checks == 1:
            raise ConnectionError("redis blip")
        return self.checks >= 3


async def test_beat_survives_a_redis_error(monkeypatch):
    monkeypatch.setattr(settings.upload, "HEARTBEAT_INTERVAL", 0.01)
    store = FlakyStore()
    beat = asyncio.create_task(
        UploadPipeline(SimpleNamespace(store=store))._beat("task")
    )
    while store.touches < 3:
        await asyncio.sleep(0.01)
    assert not beat.done()
    beat.cancel()


async def test_watch_survives_a_redis_error(monkeypatch):
    monkeypatch.setattr(pipeline, "CANCEL_POLL", 0.01)
    store = FlakyStore()
    worker = UploadPipeline(SimpleNamespace(store=store))
    await asyncio.wait_for(worker._watch("task"), 2)
    assert "task" in worker._cancelled and store.checks == 3
