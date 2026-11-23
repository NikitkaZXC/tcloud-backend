from collections.abc import AsyncGenerator

from loguru import logger
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session


class ReconnectingSession(Session):
    def _execute_internal(self, *args, **kwargs):
        if self.in_transaction():
            return super()._execute_internal(*args, **kwargs)
        try:
            return super()._execute_internal(*args, **kwargs)
        except DBAPIError as error:
            if not error.connection_invalidated:
                raise
            logger.warning("Database connection was lost, retrying: {!r}", error.orig)
            self.rollback()
            return super()._execute_internal(*args, **kwargs)


session_factory = async_sessionmaker(
    expire_on_commit=False, sync_session_class=ReconnectingSession
)


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with session_factory() as session:
        yield session
