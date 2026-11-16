import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid7

import pytest
from kombu import Connection
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import settings
from src.infrastructure.background import app
from src.infrastructure.cache import TaskStore, create_redis
from src.infrastructure.database.models import FileDB, StorageDB
from src.infrastructure.enums import TaskPriority, UploadQueue
from src.utilities.parsers import join_path
from tests.factories import make_image

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 60


class Workers:
    def __init__(self, rabbitmq_url: str, tmp_path: Path) -> None:
        self.url = rabbitmq_url
        self.tmp = tmp_path
        self.record = tmp_path / "telegram.jsonl"
        self.record.touch()
        self.processes: list[subprocess.Popen] = []

    def env(self, extra: dict) -> dict:
        return {
            **os.environ,
            "ENV_FILE": "",
            "POSTGRES_HOST": settings.db.HOST,
            "POSTGRES_PORT": str(settings.db.PORT),
            "POSTGRES_DB": settings.db.DB,
            "REDIS_HOST": settings.redis.HOST,
            "REDIS_PORT": str(settings.redis.PORT),
            "UPLOAD_TMP_DIR": str(self.tmp),
            "UPLOAD_HEARTBEAT_INTERVAL": "0.2",
            "WORKER_BROKER_URL": self.url,
            "WORKER_RECORD": str(self.record),
            "PYTHONPATH": str(ROOT),
            **extra,
        }

    async def start(
        self, queue: UploadQueue, name: str, concurrency: int = 1, **extra: str
    ) -> subprocess.Popen:
        log = open(self.tmp / f"{name}.log", "w")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "--app=tests.integration.worker_app",
                "worker",
                "--pool=threads",
                f"--concurrency={concurrency}",
                f"--queues={queue.value}",
                f"--hostname={name}@e2e",
                "--loglevel=WARNING",
                "--without-mingle",
                "--without-gossip",
            ],
            cwd=ROOT,
            env=self.env(extra),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        self.processes.append(process)
        key = f"{settings.redis.PREFIX}:worker:{queue.value}:{name}@e2e"
        redis = create_redis()
        try:
            deadline = time.monotonic() + TIMEOUT
            while not await redis.exists(key):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError((self.tmp / f"{name}.log").read_text())
                await asyncio.sleep(0.1)
        finally:
            await redis.aclose()
        return process

    def events(self, kind: str) -> list[dict]:
        lines = self.record.read_text().splitlines()
        return [event for event in map(json.loads, lines) if event["event"] == kind]

    def stop(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)


@pytest.fixture
def workers(engine, redis, rabbitmq_url, tmp_path) -> Iterator[Workers]:
    with Connection(rabbitmq_url) as connection:
        for queue in app.amqp.queues.values():
            bound = queue(connection.default_channel)
            bound.declare()
            bound.purge()
    harness = Workers(rabbitmq_url, tmp_path)
    yield harness
    harness.stop()


@pytest.fixture
def store(redis) -> TaskStore:
    return TaskStore(redis)


@pytest.fixture
async def storage_id(engine):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        storage = StorageDB(tg_chat_id=-100_777)
        session.add(storage)
        await session.commit()
        return storage.id


def publish(url: str, queue: UploadQueue, level: int, task_id, generation=0):
    with Connection(url) as connection:
        app.send_task(
            queue.value,
            kwargs={"task_id": str(task_id), "generation": generation},
            queue=queue.value,
            priority=level,
            producer=app.amqp.Producer(connection),
        )


async def queue_task(
    store, workers: Workers, storage_id, name: str, priority=TaskPriority.LOW
) -> str:
    task_id = uuid7()
    target = join_path(storage_id, "", name)
    file = workers.tmp / f"{task_id}.jpg"
    data = make_image()
    file.write_bytes(data)
    await store.create(
        {
            "id": task_id,
            "batch_id": None,
            "status": "queued",
            "stage": "scanning",
            "priority": priority,
            "priority_level": priority.level,
            "path": target,
            "bytes_done": 0,
            "bytes_total": len(data),
            "error": None,
            "warnings": [],
            "file_id": None,
            "generation": 0,
            "waiting": {"reason": f"queue:{UploadQueue.PREPARE}"},
            "params": {
                "source": "device",
                "send_as": "file",
                "on_conflict": "error",
                "callback_url": None,
                "url": None,
                "limit": 10**9,
                "file": str(file),
            },
        }
    )
    await store.activate(task_id)
    await store.lock_path(target, task_id)
    publish(workers.url, UploadQueue.PREPARE, priority.level, task_id)
    return str(task_id)


async def wait_until(store, task_id: str, predicate) -> dict:
    deadline = time.monotonic() + TIMEOUT
    task = {}
    while time.monotonic() < deadline:
        task = await store.get(task_id) or {}
        if predicate(task):
            return task
        await asyncio.sleep(0.05)
    raise AssertionError(f"condition not met: {task}")


def finished(task: dict) -> bool:
    return task.get("status") in ("done", "failed", "cancelled")


async def file_rows(engine) -> list[FileDB]:
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        return list((await session.scalars(select(FileDB))).all())


async def test_high_priority_overtakes_queued_low(workers, store, storage_id):
    await workers.start(UploadQueue.PREPARE, "prepare", concurrency=4)
    await workers.start(UploadQueue.TELEGRAM, "telegram", WORKER_TELEGRAM_DELAY="0.5")
    ids = [
        await queue_task(store, workers, storage_id, f"low{index}.jpg")
        for index in range(5)
    ]
    ids.append(
        await queue_task(store, workers, storage_id, "high.jpg", TaskPriority.HIGH)
    )
    high = await wait_until(
        store,
        ids[-1],
        lambda task: (task.get("waiting") or {}).get("reason")
        == f"queue:{UploadQueue.TELEGRAM}",
    )
    assert (await store.positions([high]))[high["id"]] == 1
    for task_id in ids:
        assert (await wait_until(store, task_id, finished))["status"] == "done"
    order = [event["name"] for event in workers.events("send")]
    assert len(order) == 6 and order.index("high.jpg") <= 1, order


async def test_raised_priority_moves_waiting_task_forward(workers, store, storage_id):
    await workers.start(UploadQueue.PREPARE, "prepare", concurrency=4)
    await workers.start(UploadQueue.TELEGRAM, "telegram", WORKER_TELEGRAM_DELAY="0.5")
    ids = [
        await queue_task(store, workers, storage_id, f"low{index}.jpg")
        for index in range(6)
    ]
    last = ids[-1]
    await wait_until(
        store,
        last,
        lambda task: (task.get("waiting") or {}).get("reason")
        == f"queue:{UploadQueue.TELEGRAM}",
    )
    generation, queue = await store.requeue(last, "high", TaskPriority.HIGH.level)
    publish(workers.url, UploadQueue(queue), TaskPriority.HIGH.level, last, generation)
    for task_id in ids:
        assert (await wait_until(store, task_id, finished))["status"] == "done"
    order = [event["name"] for event in workers.events("send")]
    assert len(order) == 6 and order.index("low5.jpg") <= 2, order


async def test_worker_killed_during_send(workers, store, storage_id, engine):
    await workers.start(UploadQueue.PREPARE, "prepare")
    first = await workers.start(
        UploadQueue.TELEGRAM, "telegram-a", WORKER_TELEGRAM_DELAY="30"
    )
    task_id = await queue_task(store, workers, storage_id, "a.jpg")
    deadline = time.monotonic() + TIMEOUT
    while not workers.events("start") and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    first.send_signal(signal.SIGKILL)
    first.wait(timeout=10)
    await workers.start(UploadQueue.TELEGRAM, "telegram-b")
    task = await wait_until(store, task_id, finished)
    assert task["status"] == "done", task
    sends = workers.events("send")
    assert len(sends) == 1 and sends[0]["pid"] != first.pid
    rows = await file_rows(engine)
    assert [row.tg_message_id for row in rows] == [sends[0]["message_id"]]
    assert list(workers.tmp.glob("*.jpg")) == []


async def test_worker_killed_after_send_before_commit(
    workers, store, storage_id, engine
):
    await workers.start(UploadQueue.PREPARE, "prepare")
    first = await workers.start(
        UploadQueue.TELEGRAM, "telegram-a", WORKER_HOLD_COMMIT="1"
    )
    task_id = await queue_task(store, workers, storage_id, "a.jpg")
    deadline = time.monotonic() + TIMEOUT
    while not workers.events("hold") and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert (await store.get(task_id))["sent"]
    first.send_signal(signal.SIGKILL)
    first.wait(timeout=10)
    await workers.start(UploadQueue.TELEGRAM, "telegram-b")
    task = await wait_until(store, task_id, finished)
    assert task["status"] == "done", task
    sends = workers.events("send")
    assert len(sends) == 1 and sends[0]["pid"] == first.pid
    assert workers.events("delete") == []
    rows = await file_rows(engine)
    assert [row.tg_message_id for row in rows] == [sends[0]["message_id"]]


async def test_queue_positions_match_real_order(workers, store, storage_id):
    await workers.start(UploadQueue.PREPARE, "prepare", concurrency=4)
    await workers.start(UploadQueue.TELEGRAM, "telegram", WORKER_TELEGRAM_DELAY="0.5")
    levels = [TaskPriority.LOW, TaskPriority.HIGH, TaskPriority.NORMAL] * 2
    ids = [
        await queue_task(store, workers, storage_id, f"t{index}.jpg", level)
        for index, level in enumerate(levels)
    ]
    in_telegram = f"queue:{UploadQueue.TELEGRAM}"
    deadline = time.monotonic() + TIMEOUT
    snapshot = {}
    while time.monotonic() < deadline and len(snapshot) != len(ids) - 1:
        tasks = await store.get_many(ids)
        waiting = [
            task
            for task in tasks
            if (task.get("waiting") or {}).get("reason") == in_telegram
        ]
        snapshot = await store.positions(waiting)
        await asyncio.sleep(0.02)
    assert sorted(snapshot.values()) == list(range(1, len(ids)))
    names = {task_id: f"t{index}.jpg" for index, task_id in enumerate(ids)}
    expected = [names[task_id] for task_id in sorted(snapshot, key=snapshot.get)]
    for task_id in ids:
        assert (await wait_until(store, task_id, finished))["status"] == "done"
    order = [event["name"] for event in workers.events("send")]
    assert [name for name in order if name in expected] == expected, (snapshot, order)
