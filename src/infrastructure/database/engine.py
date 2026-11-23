from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import settings


def create_engine() -> AsyncEngine:
    return create_async_engine(
        url=settings.db.ASYNC_URL.get_secret_value(),
        echo=settings.db.IS_DB_ECHO_LOG,
        pool_size=settings.db.POOL_SIZE,
        max_overflow=settings.db.POOL_OVERFLOW,
        pool_timeout=settings.db.TIMEOUT,
        pool_recycle=settings.db.POOL_RECYCLE,
    )
