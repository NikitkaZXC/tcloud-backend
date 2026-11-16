import threading

from loguru import logger

from src.config import settings
from src.infrastructure.cache import create_sync_redis


class Heartbeat:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self, queues: list[str], hostname: str) -> None:
        keys = [
            f"{settings.redis.PREFIX}:worker:{queue}:{hostname}" for queue in queues
        ]
        thread = threading.Thread(
            target=self._beat, args=(keys,), name="tcloud-heartbeat", daemon=True
        )
        self._threads.append(thread)
        thread.start()

    def _beat(self, keys: list[str]) -> None:
        redis = create_sync_redis()
        try:
            while not self._stop.is_set():
                ttl = max(1, int(settings.upload.HEARTBEAT_INTERVAL * 3))
                try:
                    for key in keys:
                        redis.set(key, 1, ex=ttl)
                except Exception as error:
                    logger.warning("Worker heartbeat failed: {!r}", error)
                self._stop.wait(settings.upload.HEARTBEAT_INTERVAL)
            redis.delete(*keys)
        finally:
            redis.close()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads.clear()
        self._stop.clear()
