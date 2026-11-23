import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.schemas import HealthDetailServiceRead, HealthServiceRead
from src.infrastructure.background import AppRuntime, broker_alive
from src.infrastructure.enums import TelegramSession, UploadQueue
from src.infrastructure.integrations import session_exists


class HealthService:
    def __init__(self, session: AsyncSession, runtime: AppRuntime) -> None:
        self._session = session
        self._runtime = runtime

    async def check(self) -> HealthServiceRead:
        details: dict[str, HealthDetailServiceRead] = {}

        async def probe(name: str, check: Callable[[], bool | Awaitable[bool]]) -> bool:
            started = time.perf_counter()
            result = check()
            if inspect.isawaitable(result):
                result = await result
            details[name] = HealthDetailServiceRead(
                latency_ms=round((time.perf_counter() - started) * 1000, 3)
            )
            return result

        db, telegram, telegram_download, antivirus, redis, queue = await asyncio.gather(
            probe("db", self._db),
            probe("telegram", self._runtime.telegram.ping),
            probe("telegram_download", self._runtime.download.ping),
            probe("antivirus", self._runtime.scanner.ping),
            probe("redis", self._runtime.store.ping),
            probe("queue", broker_alive),
        )
        beats = await self._runtime.store.workers() if redis else {}
        worker_telegram = beats.get(UploadQueue.TELEGRAM.value, (0.0, None))[1]
        telegram_upload = await probe(
            "telegram_upload",
            lambda: (
                session_exists(TelegramSession.UPLOAD)
                if worker_telegram is None
                else worker_telegram
            ),
        )
        now = time.time()
        workers = {item.value: item.value in beats for item in UploadQueue}
        for name in workers:
            details[name] = HealthDetailServiceRead(
                last_seen_s=(
                    round(max(0.0, now - beats[name][0]), 3) if name in beats else None
                )
            )
        return HealthServiceRead(
            ok=db
            and telegram
            and telegram_upload
            and telegram_download
            and antivirus
            and redis
            and queue
            and all(workers.values()),
            db=db,
            telegram=telegram,
            telegram_upload=telegram_upload,
            telegram_download=telegram_download,
            antivirus=antivirus,
            redis=redis,
            queue=queue,
            workers=workers,
            details=details,
        )

    async def _db(self) -> bool:
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False
