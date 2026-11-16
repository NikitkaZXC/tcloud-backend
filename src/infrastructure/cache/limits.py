from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.config import settings


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._prefix = f"{settings.redis.PREFIX}:rate"

    async def hit(self, key: str, limit: int, window: int) -> int:
        name = f"{self._prefix}:{key}"
        try:
            async with self._redis.pipeline() as pipe:
                count, _, ttl = await (
                    pipe.incr(name).expire(name, window, nx=True).ttl(name).execute()
                )
        except RedisError:
            logger.exception("Rate limit check failed, request allowed: {}", key)
            return 0
        return max(ttl, 1) if count > limit else 0
