import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.config import settings
from src.infrastructure.database.base import Base
from src.infrastructure.database.models import (
    ApiKeyDB,
    FileDB,
    FolderDB,
    FolderGrantDB,
    RefreshTokenDB,
    StorageDB,
    UserDB,
)

target_metadata = Base.metadata
MODELS = (ApiKeyDB, FileDB, FolderDB, FolderGrantDB, RefreshTokenDB, StorageDB, UserDB)
COLLATED_INDEXES = {"ix_files_name_ru", "ix_files_name_en"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (type_ == "index" and name in COLLATED_INDEXES)


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def main() -> None:
    engine = create_async_engine(settings.db.ASYNC_URL.get_secret_value(), poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


asyncio.run(main())
