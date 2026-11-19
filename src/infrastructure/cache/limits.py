from secrets import token_hex

from redis.asyncio import Redis

from src.config import settings

RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
if redis.call('TYPE', key).ok ~= 'zset' then
    redis.call('DEL', key)
end
local time = redis.call('TIME')
local now = tonumber(time[1]) * 1000 + math.floor(tonumber(time[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key) + 1
if count <= limit then
    redis.call('ZADD', key, now, now .. ':' .. ARGV[3])
end
redis.call('PEXPIRE', key, window)
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')[2]
local retry = window
if oldest then
    retry = tonumber(oldest) + window - now
end
return {count, math.ceil(retry / 1000)}
"""


class RateLimitStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._prefix = f"{settings.redis.PREFIX}:{settings.rate_limit.PREFIX}"

    async def hit(self, key: str, limit: int, window: int) -> tuple[int, int]:
        count, retry_after = await self._redis.eval(
            RATE_LIMIT_SCRIPT,
            1,
            f"{self._prefix}:{key}",
            window * 1000,
            limit,
            token_hex(8),
        )
        return int(count), int(retry_after)
