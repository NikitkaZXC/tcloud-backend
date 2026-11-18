import json
import time
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

from src.config import settings
from src.infrastructure.enums import UploadQueue

QUEUES = [queue.value for queue in UploadQueue]
ACTIVITY_TTL = 5
ACTIVITY_KINDS = ("read", "write")

UNLOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
JOB_TTL = 120


FINISHED = "local finished = {done = true, failed = true, cancelled = true}\n"

FIELD = """
local function field(name)
    local raw = redis.call('HGET', KEYS[1], name)
    if not raw then return nil end
    local value = cjson.decode(raw)
    if value == cjson.null then return nil end
    return value
end
local function waiting_queue()
    local waiting = field('waiting')
    if waiting and string.sub(waiting.reason, 1, 6) == 'queue:' then
        return string.sub(waiting.reason, 7)
    end
    return nil
end
"""

CLAIM = FINISHED + FIELD + """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if (field('generation') or 0) ~= tonumber(ARGV[2]) then return 0 end
if finished[field('status')] then return 0 end
if field('claimed') == ARGV[1] and ARGV[3] ~= '1' then return 0 end
redis.call('HSET', KEYS[1], 'claimed', cjson.encode(ARGV[1]),
    'status', cjson.encode('running'), 'waiting', 'null')
redis.call('ZREM', KEYS[2], ARGV[4])
return 1
"""

REQUEUE = FINISHED + FIELD + """
if redis.call('EXISTS', KEYS[1]) == 0 or finished[field('status')] then return nil end
local queue = waiting_queue()
if not queue or field('claimed') == queue then return nil end
local generation = (field('generation') or 0) + 1
redis.call('HSET', KEYS[1], 'generation', generation,
    'priority', cjson.encode(ARGV[1]), 'priority_level', ARGV[2])
return {generation, queue}
"""

CANCEL = FINISHED + FIELD + """
if redis.call('EXISTS', KEYS[1]) == 0 then return 'missing' end
if finished[field('status')] then return 'finished' end
redis.call('HSET', KEYS[1], 'cancel', 'true')
local queue = waiting_queue()
if queue and field('claimed') ~= queue then
    redis.call('HSET', KEYS[1], 'status', cjson.encode('cancelled'), 'waiting', 'null',
        'error', cjson.encode({code = 'cancelled', message = 'task was cancelled'}))
    return 'cancelled'
end
return 'running'
"""


DISK_SLOT = """
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then return 1 end
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[3])
local limit = tonumber(ARGV[2])
if limit > 0 and redis.call('ZCARD', KEYS[1]) >= limit then return 0 end
redis.call('ZADD', KEYS[1], ARGV[4], ARGV[1])
return 1
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
            if task.get("user_id"):
                owned = self._key("user_tasks", task["user_id"])
                pipe.zadd(owned, {str(task["id"]): now.timestamp()})
                pipe.expire(owned, settings.upload.TASK_TTL)
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

    async def statuses(self, user_id: UUID) -> list[str]:
        key = self._key("user_tasks", user_id)
        await self._redis.zremrangebyscore(
            key, "-inf", time.time() - settings.upload.TASK_TTL
        )
        task_ids = await self._redis.zrange(key, 0, -1)
        async with self._redis.pipeline(transaction=False) as pipe:
            for task_id in task_ids:
                pipe.hget(self._key("task", task_id), "status")
            rows = await pipe.execute()
        gone = [task_id for task_id, row in zip(task_ids, rows) if row is None]
        if gone:
            await self._redis.zrem(key, *gone)
        return [json.loads(row) for row in rows if row is not None]

    async def batch(self, batch_id: UUID) -> list[str]:
        return await self._redis.lrange(self._key("batch", batch_id), 0, -1)

    async def claim(
        self, task_id: UUID, queue: str, generation: int, redelivered: bool
    ) -> bool:
        return bool(
            await self._redis.eval(
                CLAIM,
                2,
                self._key("task", task_id),
                self._key("waiting", queue),
                queue,
                generation,
                int(redelivered),
                str(task_id),
            )
        )

    async def requeue(
        self, task_id: UUID, priority: str, level: int
    ) -> tuple[int, str] | None:
        result = await self._redis.eval(
            REQUEUE, 1, self._key("task", task_id), priority, level
        )
        return (int(result[0]), result[1]) if result else None

    async def cancel(self, task_id: UUID) -> str:
        return await self._redis.eval(CANCEL, 1, self._key("task", task_id))

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

    async def storage_busy(self, storage_id: UUID) -> bool:
        pattern = self._key("path", f"tcloud/{storage_id}/*")
        async for _ in self._redis.scan_iter(match=pattern, count=1000):
            return True
        return False

    async def unlock_path(self, path: str, task_id: UUID) -> None:
        await self._redis.eval(UNLOCK, 1, self._key("path", path), str(task_id))

    async def hold_job(self, name: str, owner: str) -> bool:
        key = self._key("job", name)
        if await self._redis.set(key, owner, nx=True, ex=JOB_TTL):
            return True
        return bool(await self._redis.eval(RENEW, 1, key, owner, JOB_TTL))

    async def release_job(self, name: str, owner: str) -> None:
        await self._redis.eval(UNLOCK, 1, self._key("job", name), owner)

    async def touch(self, task_id: UUID) -> None:
        await self._redis.hset(self._key("task", task_id), "beat_at", time.time())

    async def take_disk_slot(self, task_id: UUID) -> bool:
        now = time.time()
        return bool(
            await self._redis.eval(
                DISK_SLOT,
                1,
                self._key("disk"),
                str(task_id),
                settings.upload.MAX_FILES_ON_DISK,
                now - settings.upload.LOCK_TTL,
                now,
            )
        )

    async def release(self, task_id: UUID, path: str) -> None:
        await self._redis.zrem(self._key("disk"), str(task_id))
        await self.deactivate(task_id)
        await self.unlock_path(path, task_id)
        await self.leave_queues(task_id)

    async def join_queue(self, task_id: UUID, queue: str, level: int) -> None:
        score = (10 - level) * 10**13 + time.time_ns() // 1000
        await self._redis.zadd(self._key("waiting", queue), {str(task_id): score})

    async def leave_queues(self, task_id: UUID) -> None:
        async with self._redis.pipeline(transaction=False) as pipe:
            for queue in QUEUES:
                pipe.zrem(self._key("waiting", queue), str(task_id))
            await pipe.execute()

    async def positions(self, tasks: list[dict]) -> dict[str, int]:
        waiting = []
        for task in tasks:
            reason = (task.get("waiting") or {}).get("reason", "")
            if reason.startswith("queue:"):
                waiting.append((task["id"], reason.removeprefix("queue:")))
        if not waiting:
            return {}
        async with self._redis.pipeline(transaction=False) as pipe:
            for task_id, queue in waiting:
                pipe.zrank(self._key("waiting", queue), task_id)
            ranks = await pipe.execute()
        return {
            task_id: rank + 1
            for (task_id, _), rank in zip(waiting, ranks)
            if rank is not None
        }

    async def mark_activity(self, storage_id: UUID | str, kind: str) -> None:
        await self._redis.set(
            self._key("activity", storage_id, kind), 1, ex=ACTIVITY_TTL
        )

    async def activity(self, storage_ids: list[UUID]) -> dict[UUID, dict[str, bool]]:
        if not storage_ids:
            return {}
        keys = [
            self._key("activity", storage_id, kind)
            for storage_id in storage_ids
            for kind in ACTIVITY_KINDS
        ]
        flags = iter(await self._redis.mget(keys))
        return {
            storage_id: {kind: next(flags) is not None for kind in ACTIVITY_KINDS}
            for storage_id in storage_ids
        }

    async def keep_archive(self, path: str, until: float) -> None:
        await self._redis.zadd(self._key("archives"), {path: until})

    async def archives(self) -> dict[str, float]:
        return dict(
            await self._redis.zrange(self._key("archives"), 0, -1, withscores=True)
        )

    async def forget_archive(self, path: str) -> None:
        await self._redis.zrem(self._key("archives"), path)

    async def activate(self, task_id: UUID) -> None:
        await self._redis.zadd(self._key("active"), {str(task_id): time.time()})

    async def deactivate(self, task_id: UUID) -> None:
        await self._redis.zrem(self._key("active"), str(task_id))

    async def active_ids(self) -> list[str]:
        return await self._redis.zrange(self._key("active"), 0, -1)

    async def workers(self) -> set[str]:
        keys = [key async for key in self._redis.scan_iter(self._key("worker", "*"))]
        return {key.rsplit(":", 2)[1] for key in keys}

    async def count_active(self) -> int:
        key = self._key("active")
        await self._redis.zremrangebyscore(
            key, "-inf", time.time() - settings.upload.LOCK_TTL
        )
        return await self._redis.zcard(key)
