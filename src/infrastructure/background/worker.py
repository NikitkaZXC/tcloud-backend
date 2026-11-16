import asyncio
import threading
from collections.abc import Callable, Coroutine

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.infrastructure.background.disk import DiskBudget, MiB
from src.infrastructure.cache import TaskStore, create_redis
from src.infrastructure.integrations.telegram import TelegramUnauthorizedError


class WorkerRuntime:
    def __init__(
        self, telegram: Callable[[], object], scanner: Callable[[], object]
    ) -> None:
        self._make_telegram = telegram
        self._make_scanner = scanner
        self._guard = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._telegram = None
        self._telegram_lock: asyncio.Lock | None = None
        self._tasks: set[asyncio.Task] = set()
        self._redis = None
        self.engine = None
        self.session_factory: async_sessionmaker | None = None
        self.store: TaskStore | None = None
        self.scanner = None
        self.disk = DiskBudget(
            settings.upload.TMP_DIR, settings.upload.MIN_FREE_DISK_MB * MiB
        )
        self.telegram_resume_at = 0.0
        self.handoff_lock: asyncio.Lock | None = None

    def run(self, coroutine: Coroutine):
        return asyncio.run_coroutine_threadsafe(
            self._with_resources(coroutine), self._start()
        ).result()

    def _start(self) -> asyncio.AbstractEventLoop:
        with self._guard:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                threading.Thread(
                    target=self._loop.run_forever, name="tcloud-worker", daemon=True
                ).start()
            return self._loop

    async def _with_resources(self, coroutine: Coroutine):
        if self.store is None:
            self.engine = create_async_engine(
                settings.db.ASYNC_URL.get_secret_value(), poolclass=NullPool
            )
            self.session_factory = async_sessionmaker(
                self.engine, expire_on_commit=False
            )
            self._redis = create_redis()
            self.store = TaskStore(self._redis)
            self.scanner = self._make_scanner()
            self.handoff_lock = asyncio.Lock()
        return await coroutine

    async def telegram(self):
        if self._telegram is None:
            self._telegram_lock = self._telegram_lock or asyncio.Lock()
            async with self._telegram_lock:
                if self._telegram is None:
                    client = self._make_telegram()
                    if not await client.start():
                        await client.forget()
                        raise TelegramUnauthorizedError(
                            "worker Telegram session is not authorized: "
                            "log in with /api/telegram/worker"
                        )
                    self._telegram = client
        return self._telegram

    async def forget_telegram(self) -> None:
        client, self._telegram = self._telegram, None
        if client:
            await client.stop()

    def spawn(self, coroutine: Coroutine) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _close(self) -> None:
        await self.drain()
        if self._telegram:
            await self._telegram.stop()
        if self._redis:
            await self._redis.aclose()
        if self.engine:
            await self.engine.dispose()

    def shutdown(self) -> None:
        with self._guard:
            loop, self._loop = self._loop, None
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._close(), loop).result()
        loop.call_soon_threadsafe(loop.stop)
        self._telegram = self._telegram_lock = self._redis = None
        self.engine = self.session_factory = self.store = self.scanner = None
