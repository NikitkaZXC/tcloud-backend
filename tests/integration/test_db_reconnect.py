from uuid import uuid7

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.infrastructure.database.engine import create_engine
from src.infrastructure.database.models import FolderGrantDB, StorageDB, UserDB
from src.infrastructure.database.repositories import UserRepository
from src.infrastructure.database.session import ReconnectingSession

KILL = text(
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
)


async def test_first_query_after_postgres_restart_reconnects(engine):
    pooled = create_engine()
    sessions = async_sessionmaker(
        pooled, expire_on_commit=False, sync_session_class=ReconnectingSession
    )
    try:
        async with sessions() as session:
            assert await session.scalar(text("SELECT 1")) == 1
        async with engine.connect() as admin:
            await admin.execute(KILL)
        async with sessions() as session:
            assert await session.scalar(text("SELECT 2")) == 2
        async with engine.connect() as admin:
            await admin.execute(KILL)
        async with sessions() as session:
            assert await session.get(UserDB, uuid7()) is None
    finally:
        await pooled.dispose()


async def test_user_and_grants_come_in_one_query(engine):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        user = UserDB(login="bob", password_hash="x", role="user")
        storages = [StorageDB(tg_chat_id=-1001), StorageDB(tg_chat_id=-1002)]
        session.add_all([user, *storages])
        await session.flush()
        session.add_all(
            [
                FolderGrantDB(
                    user_id=user.id,
                    storage_id=storage.id,
                    path=path,
                    recursive=True,
                    can_read=True,
                    can_upload=False,
                    can_delete=False,
                )
                for storage in storages
                for path in ("b", "a")
            ]
        )
        await session.commit()
    statements = []

    def listener(*args):
        statements.append(args[2])

    event.listen(engine.sync_engine, "before_cursor_execute", listener)
    try:
        async with sessions() as session:
            found, grants = await UserRepository(session).get_with_grants(user.id)
            missing = await UserRepository(session).get_with_grants(storages[0].id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
    assert found.login == "bob" and missing is None and len(statements) == 2
    assert [(grant.storage_id, grant.path) for grant in grants] == sorted(
        (storage.id, path) for storage in storages for path in ("a", "b")
    )
