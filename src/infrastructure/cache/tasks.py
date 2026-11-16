import json
import time
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

from src.config import settings

UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def encode(value) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


class TaskStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._prefix = settings.redis.PREFIX

    def _key(self, *parts: object) -> str:
        return ":".join((self._prefix, *(str(part) for part in parts)))

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    async def create(self, task: dict) -> None:
        now = datetime.now(UTC)
        task = {**task, "created_at": now, "updated_at": now}
        key = self._key("task", task["id"])
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                key, mapping={name: encode(value) for name, value in task.items()}
            )
            pipe.expire(key, settings.upload.TASK_TTL)
            if task.get("batch_id"):
                batch = self._key("batch", task["batch_id"])
                pipe.rpush(batch, str(task["id"]))
                pipe.expire(batch, settings.upload.TASK_TTL)
            await pipe.execute()

    async def update(self, task_id: UUID, **values) -> None:
        values["updated_at"] = datetime.now(UTC)
        key = self._key("task", task_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                key, mapping={name: encode(value) for name, value in values.items()}
            )
            pipe.expire(key, settings.upload.TASK_TTL)
            await pipe.execute()

    async def get(self, task_id: UUID) -> dict | None:
        raw = await self._redis.hgetall(self._key("task", task_id))
        return {name: json.loads(value) for name, value in raw.items()} or None

    async def get_many(self, task_ids: list[str]) -> list[dict]:
        async with self._redis.pipeline(transaction=False) as pipe:
            for task_id in task_ids:
                pipe.hgetall(self._key("task", task_id))
            rows = await pipe.execute()
        return [
            {name: json.loads(value) for name, value in row.items()}
            for row in rows
            if row
        ]

    async def batch(self, batch_id: UUID) -> list[str]:
        return await self._redis.lrange(self._key("batch", batch_id), 0, -1)

    async def lock_path(self, path: str, task_id: UUID) -> bool:
        return bool(
            await self._redis.set(
                self._key("path", path),
                str(task_id),
                nx=True,
                ex=settings.upload.LOCK_TTL,
            )
        )

    async def path_locked(self, path: str) -> bool:
        return bool(await self._redis.exists(self._key("path", path)))

    async def unlock_path(self, path: str, task_id: UUID) -> None:
        await self._redis.eval(UNLOCK, 1, self._key("path", path), str(task_id))

    async def activate(self, task_id: UUID) -> None:
        await self._redis.zadd(self._key("active"), {str(task_id): time.time()})

    async def deactivate(self, task_id: UUID) -> None:
        await self._redis.zrem(self._key("active"), str(task_id))

    async def count_active(self) -> int:
        key = self._key("active")
        await self._redis.zremrangebyscore(
            key, "-inf", time.time() - settings.upload.LOCK_TTL
        )
        return await self._redis.zcard(key)
