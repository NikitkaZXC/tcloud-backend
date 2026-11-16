from celery import Celery
from kombu import Queue

from src.config import settings
from src.infrastructure.enums import TaskPriority, UploadQueue

MAX_PRIORITY = TaskPriority.HIGH.level

app = Celery(
    main=settings.app.TITLE,
    broker=settings.rabbitmq.URL.get_secret_value(),
    include=["src.application.tasks"],
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
    worker_send_task_events=True,
    task_send_sent_event=True,
    task_queues=[
        Queue(
            queue.value,
            routing_key=queue.value,
            queue_arguments={"x-max-priority": MAX_PRIORITY},
        )
        for queue in UploadQueue
    ],
    task_routes={queue.value: {"queue": queue.value} for queue in UploadQueue},
    task_queue_max_priority=MAX_PRIORITY,
    task_default_priority=TaskPriority.NORMAL.level,
)
