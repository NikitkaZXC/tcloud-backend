from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import GrantNotFoundError
from src.application.schemas import Actor, GrantServiceCreate, GrantServiceRead
from src.application.services.storage import StorageService
from src.application.services.user import UserService
from src.infrastructure.database.repositories import GrantRepository
from src.utilities.parsers import split_folder


class GrantService:
    def __init__(
        self,
        session: AsyncSession,
        user_service: UserService,
        storage_service: StorageService,
    ) -> None:
        self._session = session
        self._users = user_service
        self._storages = storage_service
        self._repo = GrantRepository(session)

    async def get_all(self, actor: Actor, user_id: UUID) -> list[GrantServiceRead]:
        await self._users.get_manageable(actor, user_id)
        return [
            GrantServiceRead.from_db(grant)
            for grant in await self._repo.get_by_user(user_id)
        ]

    async def put(
        self, actor: Actor, user_id: UUID, data: GrantServiceCreate
    ) -> GrantServiceRead:
        await self._users.get_manageable(actor, user_id)
        storage_id, folder = split_folder(data.path)
        await self._storages.get_db(storage_id)
        values = data.model_dump(exclude={"path"})
        grant = await self._repo.get_by_path(user_id, storage_id, folder)
        if grant:
            for name, value in values.items():
                setattr(grant, name, value)
        else:
            grant = await self._repo.create(
                user_id=user_id, storage_id=storage_id, path=folder, **values
            )
        await self._session.commit()
        logger.info(
            "Grant {} on {} set for user {} by {}",
            grant.id,
            data.path,
            user_id,
            actor.user_id,
        )
        return GrantServiceRead.from_db(await self._repo.get_by_id(grant.id))

    async def delete(self, actor: Actor, user_id: UUID, grant_id: UUID) -> None:
        await self._users.get_manageable(actor, user_id)
        grant = await self._repo.get_by_id(grant_id)
        if not grant or grant.user_id != user_id:
            raise GrantNotFoundError("grant not found")
        await self._repo.delete(grant)
        await self._session.commit()
        logger.info("Grant {} deleted by {}", grant_id, actor.user_id)
