from typing import Any
from uuid import UUID

from celery.signals import worker_shutdown

from src.application.services.pipeline import UploadPipeline
from src.config import settings
from src.infrastructure.background import WorkerRuntime, app
from src.infrastructure.enums import UploadQueue
from src.infrastructure.integrations import create_scanner, create_telegram

runtime = WorkerRuntime(
    telegram=lambda: create_telegram(settings.telegram.WORKER_SESSION),
    scanner=create_scanner,
)


async def _prepare(task_id: str) -> None:
    await UploadPipeline(runtime).prepare(UUID(task_id))


async def _send(task_id: str, payload: dict) -> None:
    await UploadPipeline(runtime).send(UUID(task_id), payload)


@app.task(name=UploadQueue.PREPARE.value)
def prepare_upload(task_id: str) -> None:
    runtime.run(_prepare(task_id))


@app.task(name=UploadQueue.TELEGRAM.value)
def send_upload(task_id: str, payload: dict) -> None:
    runtime.run(_send(task_id, payload))


@worker_shutdown.connect
def on_worker_shutdown(**_: Any) -> None:
    runtime.shutdown()
