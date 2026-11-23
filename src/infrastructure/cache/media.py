import time
from collections import OrderedDict

MEDIA_TTL = 90.0
MEDIA_MAX = 2000


class MediaCache:
    def __init__(self, ttl: float = MEDIA_TTL, limit: int = MEDIA_MAX) -> None:
        self._ttl = ttl
        self._limit = limit
        self._items: OrderedDict[tuple[int, int], tuple[float, object, int]] = (
            OrderedDict()
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
