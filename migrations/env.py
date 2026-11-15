import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from tcloud.db import Base

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(os.environ["TCLOUD_DATABASE_URL"])
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


asyncio.run(main())
