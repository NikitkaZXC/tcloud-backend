import asyncio

from kombu.exceptions import KombuError

from src.infrastructure.background.celery.app import app
from src.infrastructure.background.exception import QueueUnavailableError
from src.infrastructure.enums import UploadQueue


async def enqueue(queue: UploadQueue, priority: int, **kwargs) -> None:
    try:
        await asyncio.to_thread(
            app.send_task,
            queue.value,
            kwargs=kwargs,
            queue=queue.value,
            priority=priority,
        )
    except (KombuError, OSError) as error:
        raise QueueUnavailableError("upload queue is not available") from error
