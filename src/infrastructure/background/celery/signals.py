from typing import Any

from celery.signals import setup_logging, task_failure, worker_ready
from loguru import logger

from src.infrastructure.logger import setup_logging as configure_logging


@setup_logging.connect
def on_setup_logging(**_: Any) -> None:
    configure_logging()


@worker_ready.connect
def on_worker_ready(sender: Any = None, **_: Any) -> None:
    logger.info("Celery worker ready: {}", getattr(sender, "hostname", sender))


@task_failure.connect
def on_task_failure(
    sender: Any = None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    **_: Any,
) -> None:
    logger.opt(exception=exception).error(
        "Celery task failed: {} [{}]: {!r}", sender.name, task_id, exception
    )
