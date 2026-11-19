from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models import UserDB
from src.infrastructure.enums import UserRole


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> UserDB | None:
        return await self._session.get(UserDB, user_id, populate_existing=True)

    async def get_for_update(self, user_id: UUID) -> UserDB | None:
        return await self._session.scalar(
            select(UserDB)
            .where(UserDB.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def get_by_login(self, login: str) -> UserDB | None:
        return await self._session.scalar(select(UserDB).where(UserDB.login == login))

    async def get_root(self) -> UserDB | None:
        return await self._session.scalar(
            select(UserDB).where(UserDB.role == UserRole.ROOT)
        )

    async def get_all(self) -> list[UserDB]:
        result = await self._session.scalars(
            select(UserDB).order_by(UserDB.created_at, UserDB.id)
        )
        return list(result.all())

    async def create(self, login: str, password_hash: str, role: str) -> UserDB:
        user = UserDB(login=login, password_hash=password_hash, role=role)
        self._session.add(user)
        await self._session.flush()
        return user

    async def delete(self, user: UserDB) -> None:
        await self._session.delete(user)
        await self._session.flush()

    async def save(self) -> None:
        await self._session.flush()
