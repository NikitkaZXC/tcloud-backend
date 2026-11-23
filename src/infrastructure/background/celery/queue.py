import asyncio
import time

from kombu.exceptions import KombuError
from loguru import logger

from src.infrastructure.background.celery.app import app
from src.infrastructure.background.exception import QueueUnavailableError
from src.infrastructure.enums import UploadQueue

BROKER_TTL = 5.0
_alive: tuple[float, bool] | None = None


async def enqueue(
    queue: UploadQueue, priority: int, task: str | None = None, **kwargs
) -> None:
    try:
        await asyncio.to_thread(
            app.send_task,
            task or queue.value,
            kwargs=kwargs,
            queue=queue.value,
            priority=priority,
        )
    except (KombuError, OSError) as error:
        raise QueueUnavailableError("upload queue is not available") from error


def _sizes() -> dict[str, int]:
    with app.connection_for_read() as connection:
        channel = connection.default_channel
        return {
            queue.value: channel.queue_declare(queue.value, passive=True).message_count
            for queue in UploadQueue
        }


async def queue_sizes() -> dict[str, int] | None:
    try:
        return await asyncio.to_thread(_sizes)
    except Exception as error:
        logger.warning("Cannot read queue sizes: {!r}", error)
        return None


async def broker_alive() -> bool:
    global _alive
    if _alive and time.monotonic() - _alive[0] < BROKER_TTL:
        return _alive[1]
    _alive = (time.monotonic(), await queue_sizes() is not None)
    return _alive[1]
