import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.infrastructure.database.base import Base
from src.infrastructure.database.models import FileDB, FolderDB, StorageDB, TaskDB

target_metadata = Base.metadata
MODELS = (FileDB, FolderDB, StorageDB, TaskDB)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(settings.db.ASYNC_URL.get_secret_value(), poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


asyncio.run(main())
