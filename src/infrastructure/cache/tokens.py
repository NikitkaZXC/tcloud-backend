from uuid import UUID

from redis.asyncio import Redis

from src.config import settings


class TokenBlacklist:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._prefix = f"{settings.redis.PREFIX}:revoked"

    async def revoke(self, jti: UUID) -> None:
        await self._redis.set(
            f"{self._prefix}:{jti}",
            1,
            ex=settings.jwt.ACCESS_TOKEN_EXPIRATION_MINUTES * 60,
        )

    async def revoked(self, jti: UUID) -> bool:
        return bool(await self._redis.exists(f"{self._prefix}:{jti}"))
