from redis.asyncio import Redis

from src.config import settings


def create_redis() -> Redis:
    return Redis(
        host=settings.redis.HOST,
        port=settings.redis.PORT,
        password=settings.redis.PASSWORD.get_secret_value() or None,
        db=settings.redis.DB,
        decode_responses=True,
    )
