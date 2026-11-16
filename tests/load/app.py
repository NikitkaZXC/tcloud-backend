import os
from contextlib import ExitStack

from celery.contrib.testing.worker import start_worker

import main
from src.application.tasks import upload as upload_tasks
from src.infrastructure.background import WorkerRuntime, app
from src.infrastructure.enums import UploadQueue
from tests.fakes import FakeScanner, FakeTelegram

telegram = FakeTelegram()
telegram.delay = float(os.environ.get("LOAD_TELEGRAM_DELAY", "0.2"))
main.create_telegram = lambda: telegram
main.create_scanner = FakeScanner
upload_tasks.runtime = WorkerRuntime(telegram=lambda: telegram, scanner=FakeScanner)

app.conf.broker_url = "memory://"
app.conf.broker_transport_options = {"polling_interval": 0.01}
workers = ExitStack()
for queue, concurrency in ((UploadQueue.PREPARE, 4), (UploadQueue.TELEGRAM, 2)):
    workers.enter_context(
        start_worker(
            app,
            concurrency=concurrency,
            pool="threads",
            perform_ping_check=False,
            queues=[queue.value],
            hostname=f"{queue.value}@load",
        )
    )

app = main.app
