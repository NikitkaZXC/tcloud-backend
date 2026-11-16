from uuid import UUID

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    ForbiddenError,
    UserExistsError,
    UserNotFoundError,
)
from src.application.schemas import (
    Actor,
    UserServiceCreate,
    UserServiceRead,
    UserServiceUpdate,
)
from src.application.services.access import require_admin, require_root
from src.application.services.user_auth import UserAuthService
from src.config import settings
from src.infrastructure.database.models import UserDB
from src.infrastructure.database.repositories import UserRepository
from src.infrastructure.enums import UserRole
from src.infrastructure.security import hash_password, verify_password


class UserService:
    def __init__(self, session: AsyncSession, auth_service: UserAuthService) -> None:
        self._session = session
        self._auth = auth_service
        self._repo = UserRepository(session)

    async def get_db(self, user_id: UUID) -> UserDB:
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise UserNotFoundError("user not found")
        return user

    async def get_manageable(
        self, actor: Actor, user_id: UUID, allow_self: bool = False
    ) -> UserDB:
        require_admin(actor)
        user = await self.get_db(user_id)
        if user.id == actor.user_id:
            if not allow_self:
                raise ForbiddenError("change your own account through /api/auth")
            return user
        if user.role == UserRole.ROOT:
            raise ForbiddenError("root is managed through ROOT_LOGIN and ROOT_PASSWORD")
        if user.role == UserRole.ADMIN:
            require_root(actor)
        return user

    async def ensure_root(self) -> None:
        login = settings.root.LOGIN
        password = settings.root.PASSWORD.get_secret_value()
        root = await self._repo.get_root()
        taken = await self._repo.get_by_login(login)
        if taken and (not root or taken.id != root.id):
            raise UserExistsError(f"ROOT_LOGIN {login} is taken by another user")
        if not root:
            root = await self._repo.create(
                login, await hash_password(password), UserRole.ROOT
            )
            await self._session.commit()
            logger.info("Root user {} created", login)
            return
        root.login = login
        root.blocked = False
        if not await verify_password(password, root.password_hash):
            root.password_hash = await hash_password(password)
            await self._auth.revoke_all(root.id)
            logger.info("Root password changed from ROOT_PASSWORD, sessions revoked")
        await self._session.commit()

    async def me(self, actor: Actor) -> UserServiceRead:
        return UserServiceRead.from_db(await self.get_db(actor.user_id))

    async def get_all(self, actor: Actor) -> list[UserServiceRead]:
        require_admin(actor)
        return [UserServiceRead.from_db(user) for user in await self._repo.get_all()]

    async def get(self, actor: Actor, user_id: UUID) -> UserServiceRead:
        require_admin(actor)
        return UserServiceRead.from_db(await self.get_db(user_id))

    async def create(self, actor: Actor, data: UserServiceCreate) -> UserServiceRead:
        require_admin(actor)
        if data.role == UserRole.ROOT:
            raise ForbiddenError("there is only one root, set by ROOT_LOGIN")
        if data.role == UserRole.ADMIN:
            require_root(actor)
        if await self._repo.get_by_login(data.login):
            raise UserExistsError(f"login {data.login} is taken")
        try:
            user = await self._repo.create(
                data.login, await hash_password(data.password), data.role
            )
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise UserExistsError(f"login {data.login} is taken") from None
        logger.info("User {} ({}) created by {}", user.id, user.role, actor.user_id)
        return UserServiceRead.from_db(user)

    async def update(
        self, actor: Actor, user_id: UUID, data: UserServiceUpdate
    ) -> UserServiceRead:
        user = await self.get_manageable(actor, user_id)
        if data.role == UserRole.ROOT:
            raise ForbiddenError("there is only one root, set by ROOT_LOGIN")
        if data.role == UserRole.ADMIN:
            require_root(actor)
        revoke = False
        if data.role is not None and data.role != user.role:
            user.role, revoke = data.role, True
        if data.blocked is not None:
            user.blocked = data.blocked
            revoke = revoke or data.blocked
        if data.password is not None:
            user.password_hash = await hash_password(data.password)
            user.failed_logins, user.locked_until, revoke = 0, None, True
        if revoke:
            await self._auth.revoke_all(user.id)
        await self._session.commit()
        logger.info("User {} updated by {}", user.id, actor.user_id)
        return UserServiceRead.from_db(await self.get_db(user.id))

    async def delete(self, actor: Actor, user_id: UUID) -> None:
        user = await self.get_manageable(actor, user_id)
        await self._auth.revoke_all(user.id)
        await self._repo.delete(user)
        await self._session.commit()
        logger.info("User {} deleted by {}", user_id, actor.user_id)
