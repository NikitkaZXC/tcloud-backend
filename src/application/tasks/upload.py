from typing import Any
from uuid import UUID

from celery import Task
from celery.signals import worker_ready, worker_shutdown

from src.application.services.pipeline import UploadPipeline
from src.config import settings
from src.infrastructure.background import Heartbeat, WorkerRuntime, app
from src.infrastructure.enums import UploadQueue
from src.infrastructure.integrations import create_scanner, create_telegram

runtime = WorkerRuntime(
    telegram=lambda: create_telegram(settings.telegram.WORKER_SESSION),
    scanner=create_scanner,
)
heartbeat = Heartbeat()


async def _run(
    queue: UploadQueue, task_id: str, generation: int, redelivered: bool
) -> None:
    await UploadPipeline(runtime).run(queue, UUID(task_id), generation, redelivered)


def _execute(task: Task, queue: UploadQueue, task_id: str, generation: int) -> None:
    redelivered = bool((task.request.delivery_info or {}).get("redelivered"))
    runtime.run(_run(queue, task_id, generation, redelivered))


@app.task(name=UploadQueue.DOWNLOAD.value, bind=True)
def download_upload(self: Task, task_id: str, generation: int = 0) -> None:
    _execute(self, UploadQueue.DOWNLOAD, task_id, generation)


@app.task(name=UploadQueue.PREPARE.value, bind=True)
def prepare_upload(self: Task, task_id: str, generation: int = 0) -> None:
    _execute(self, UploadQueue.PREPARE, task_id, generation)


@app.task(name=UploadQueue.TELEGRAM.value, bind=True)
def send_upload(self: Task, task_id: str, generation: int = 0) -> None:
    _execute(self, UploadQueue.TELEGRAM, task_id, generation)


@worker_ready.connect
def on_worker_ready(sender: Any = None, **_: Any) -> None:
    heartbeat.start(
        [queue.name for queue in sender.task_consumer.queues], sender.hostname
    )


@worker_shutdown.connect
def on_worker_shutdown(**_: Any) -> None:
    heartbeat.stop()
    runtime.shutdown()
