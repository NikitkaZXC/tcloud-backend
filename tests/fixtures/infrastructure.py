import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.infrastructure.cache import create_redis
from src.infrastructure.database.base import Base

ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "postgres:18"
REDIS_IMAGE = "redis:8"
RABBITMQ_IMAGE = "rabbitmq:4-alpine"
MAINTENANCE_DB = "postgres"


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
    except Exception:
        return False
    return True


async def _admin_execute(statement: str) -> None:
    connection = await asyncpg.connect(
        host=settings.db.HOST,
        port=settings.db.PORT,
        user=settings.db.USER.get_secret_value(),
        password=settings.db.PASSWORD.get_secret_value(),
        database=MAINTENANCE_DB,
    )
    try:
        await connection.execute(statement)
    finally:
        await connection.close()


@contextmanager
def own_database() -> Iterator[str]:
    name = f"{settings.db.DB}_{uuid4().hex[:8]}"
    asyncio.run(_admin_execute(f'CREATE DATABASE "{name}"'))
    shared, settings.db.DB = settings.db.DB, name
    try:
        yield name
    finally:
        settings.db.DB = shared
        asyncio.run(_admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def containers() -> Iterator[None]:
    if os.environ.get("TEST_POSTGRES_HOST"):
        settings.db.HOST = os.environ["TEST_POSTGRES_HOST"]
        settings.db.PORT = int(os.environ.get("TEST_POSTGRES_PORT", "5432"))
        with own_database():
            yield
        return

    if not _docker_available():
        pytest.skip("Docker is not available")

    from testcontainers.community.postgres import PostgresContainer

    postgres = PostgresContainer(
        POSTGRES_IMAGE,
        username=settings.db.USER.get_secret_value(),
        password=settings.db.PASSWORD.get_secret_value(),
        dbname=settings.db.DB,
        driver=None,
    )
    with postgres:
        settings.db.HOST = postgres.get_container_host_ip()
        settings.db.PORT = int(postgres.get_exposed_port(5432))
        yield


@pytest.fixture(scope="session")
def alembic_config(containers: None) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(ROOT / "src/infrastructure/database/migrations")
    )
    return config


@pytest.fixture(scope="session")
def migrated(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture
async def engine(migrated: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        settings.db.ASYNC_URL.get_secret_value(), poolclass=NullPool
    )
    tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def redis_server() -> Iterator[None]:
    if os.environ.get("TEST_REDIS_HOST"):
        settings.redis.HOST = os.environ["TEST_REDIS_HOST"]
        settings.redis.PORT = int(os.environ.get("TEST_REDIS_PORT", "6379"))
        yield
        return

    if not _docker_available():
        pytest.skip("Docker is not available")

    from testcontainers.community.redis import RedisContainer

    with RedisContainer(REDIS_IMAGE) as redis:
        settings.redis.HOST = redis.get_container_host_ip()
        settings.redis.PORT = int(redis.get_exposed_port(6379))
        yield


@pytest.fixture
async def redis(redis_server: None) -> AsyncIterator[Redis]:
    client = create_redis()
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture(scope="session")
def rabbitmq_url() -> Iterator[str]:
    if os.environ.get("TEST_RABBITMQ_URL"):
        yield os.environ["TEST_RABBITMQ_URL"]
        return

    if not _docker_available():
        pytest.skip("Docker is not available")

    from testcontainers.community.rabbitmq import RabbitMqContainer

    with RabbitMqContainer(RABBITMQ_IMAGE) as rabbitmq:
        host = rabbitmq.get_container_host_ip()
        port = rabbitmq.get_exposed_port(5672)
        yield f"amqp://guest:guest@{host}:{port}//"
