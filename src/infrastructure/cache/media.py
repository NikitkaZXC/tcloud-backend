import asyncio
import time
from collections import OrderedDict

from src.infrastructure.integrations import TelegramMessageGoneError

MEDIA_TTL = 90.0
MEDIA_MAX = 2000
MEDIA_WINDOW = 0.02


class MediaCache:
    def __init__(self, ttl: float = MEDIA_TTL, limit: int = MEDIA_MAX) -> None:
        self._ttl = ttl
        self._limit = limit
        self._items: OrderedDict[tuple[int, int], tuple[float, object, int]] = (
            OrderedDict()
        )
        self._batches: dict[int, dict[int, asyncio.Future]] = {}
        self._flushes: set[asyncio.Task] = set()

    async def load(self, telegram, chat_id: int, message_id: int) -> tuple[object, int]:
        found = self.get(chat_id, message_id)
        if found is not None:
            return found
        batch = self._batches.setdefault(chat_id, {})
        future = batch.get(message_id)
        if future is None:
            future = batch[message_id] = asyncio.get_running_loop().create_future()
            if len(batch) == 1:
                flush = asyncio.create_task(self._flush(telegram, chat_id))
                self._flushes.add(flush)
                flush.add_done_callback(self._flushes.discard)
        return await asyncio.shield(future)

    async def _flush(self, telegram, chat_id: int) -> None:
        batch: dict[int, asyncio.Future] = {}
        try:
            await asyncio.sleep(MEDIA_WINDOW)
            batch = self._batches.pop(chat_id)
            found = await telegram.messages_media(chat_id, list(batch))
        except BaseException as error:
            for future in (batch or self._batches.pop(chat_id, {})).values():
                if future.done():
                    continue
                if isinstance(error, Exception):
                    future.set_exception(error)
                else:
                    future.cancel()
            raise
        for message_id, future in batch.items():
            if message_id in found:
                self.put(chat_id, message_id, *found[message_id])
                future.set_result(found[message_id])
            else:
                future.set_exception(
                    TelegramMessageGoneError("telegram message with this file is gone")
                )

    def get(self, chat_id: int, message_id: int) -> tuple[object, int] | None:
        key = (chat_id, message_id)
        item = self._items.get(key)
        if item is None:
            return None
        if item[0] < time.monotonic():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return item[1], item[2]

    def put(self, chat_id: int, message_id: int, media: object, size: int) -> None:
        key = (chat_id, message_id)
        self._items[key] = (time.monotonic() + self._ttl, media, size)
        self._items.move_to_end(key)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def forget(self, chat_id: int, message_ids: list[int]) -> None:
        for message_id in message_ids:
            self._items.pop((chat_id, message_id), None)
