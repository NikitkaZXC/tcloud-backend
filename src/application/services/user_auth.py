from datetime import UTC, datetime, timedelta
from uuid import UUID

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.exception import (
    AccountLockedError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    NotAuthenticatedError,
    RateLimitedError,
    UserBlockedError,
    UserSessionNotFoundError,
)
from src.application.schemas import (
    Actor,
    ClientInfo,
    GrantServiceRead,
    SettingsServiceRead,
    TokenPair,
    UserSessionServiceRead,
)
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.models import RefreshTokenDB, UserDB
from src.infrastructure.database.repositories import (
    ApiKeyRepository,
    GrantRepository,
    RefreshTokenRepository,
    UserRepository,
)
from src.infrastructure.enums import UserRole
from src.infrastructure.security import (
    DUMMY_HASH,
    TokenInvalidError,
    decode_access_token,
    generate_access_token,
    generate_refresh_token,
    hash_password,
    hash_secret,
    verify_password,
)

KEY_TOUCH_INTERVAL = timedelta(minutes=1)


class UserAuthService:
    def __init__(self, session: AsyncSession, runtime: AppRuntime) -> None:
        self._session = session
        self._blacklist = runtime.blacklist
        self._limiter = runtime.limiter
        self._users = UserRepository(session)
        self._tokens = RefreshTokenRepository(session)
        self._keys = ApiKeyRepository(session)
        self._grants = GrantRepository(session)

    async def _issue(
        self, user: UserDB, client: ClientInfo, signed_in_at: datetime | None = None
    ) -> TokenPair:
        access_token, payload = generate_access_token(user.id, user.role)
        refresh_token = generate_refresh_token()
        now = datetime.now(UTC)
        await self._tokens.create(
            user_id=user.id,
            token_hash=hash_secret(refresh_token),
            access_jti=payload.jti,
            user_agent=client.user_agent,
            ip_address=client.ip_address,
            signed_in_at=signed_in_at or now,
            expires_at=now
            + timedelta(minutes=settings.jwt.REFRESH_TOKEN_EXPIRATION_MINUTES),
            revoked=False,
        )
        return TokenPair(access_token=access_token, refresh_token=refresh_token)

    async def _revoke(self, token: RefreshTokenDB) -> None:
        token.revoked = True
        await self._blacklist.revoke(token.access_jti)

    async def revoke_all(self, user_id: UUID) -> int:
        tokens = await self._tokens.get_active(user_id)
        for token in tokens:
            await self._revoke(token)
        await self._session.flush()
        return len(tokens)

    async def signin(self, login: str, password: str, client: ClientInfo) -> TokenPair:
        retry_after = await self._limiter.hit(
            f"signin:{client.ip_address}",
            settings.jwt.SIGNIN_IP_LIMIT,
            settings.jwt.SIGNIN_IP_WINDOW,
        )
        if retry_after:
            logger.warning("Sign in rate limited: {}", client.ip_address)
            raise RateLimitedError(
                "too many sign in attempts, try later", retry_after=retry_after
            )
        user = await self._users.get_by_login(login)
        if not user:
            await verify_password(password, DUMMY_HASH)
            logger.warning("Sign in failed: unknown login {}", login)
            raise InvalidCredentialsError("wrong login or password")
        now = datetime.now(UTC)
        if user.locked_until and user.locked_until > now:
            raise AccountLockedError(
                "too many wrong passwords, try later",
                retry_after=max(int((user.locked_until - now).total_seconds()), 1),
            )
        if not await verify_password(password, user.password_hash):
            user.failed_logins += 1
            locked = user.failed_logins >= settings.jwt.MAX_FAILED_LOGINS
            if locked:
                user.failed_logins = 0
                user.locked_until = now + timedelta(minutes=settings.jwt.LOCK_MINUTES)
            await self._session.commit()
            logger.warning("Sign in failed: wrong password for user {}", user.id)
            if locked:
                raise AccountLockedError(
                    "too many wrong passwords, try later",
                    retry_after=settings.jwt.LOCK_MINUTES * 60,
                )
            raise InvalidCredentialsError("wrong login or password")
        if user.blocked:
            logger.warning("Sign in rejected: user {} is blocked", user.id)
            raise UserBlockedError("user is blocked")
        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = now
        await self._tokens.delete_expired(user.id)
        pair = await self._issue(user, client)
        await self._session.commit()
        logger.info("User {} signed in from {}", user.id, client.ip_address)
        return pair

    async def refresh(self, refresh_token: str, client: ClientInfo) -> TokenPair:
        token = await self._tokens.get_by_hash(hash_secret(refresh_token))
        if not token or token.expires_at <= datetime.now(UTC):
            raise InvalidRefreshTokenError("refresh token is invalid or expired")
        if token.revoked:
            revoked = await self.revoke_all(token.user_id)
            await self._session.commit()
            logger.warning(
                "Refresh token reuse for user {}: {} sessions revoked",
                token.user_id,
                revoked,
            )
            raise InvalidRefreshTokenError("refresh token is invalid or expired")
        user = await self._users.get_by_id(token.user_id)
        if user.blocked:
            await self.revoke_all(user.id)
            await self._session.commit()
            raise UserBlockedError("user is blocked")
        await self._revoke(token)
        pair = await self._issue(user, client, token.signed_in_at)
        await self._session.commit()
        return pair

    async def logout(self, refresh_token: str) -> None:
        token = await self._tokens.get_by_hash(hash_secret(refresh_token))
        if not token or token.revoked:
            return
        await self._revoke(token)
        await self._session.commit()
        logger.info("User {} logged out", token.user_id)

    async def logout_all(self, actor: Actor) -> None:
        revoked = await self.revoke_all(actor.user_id)
        await self._session.commit()
        logger.info("User {} logged out of {} sessions", actor.user_id, revoked)

    async def sessions(self, actor: Actor) -> list[UserSessionServiceRead]:
        return [
            UserSessionServiceRead.from_db(token, actor.jti)
            for token in await self._tokens.get_active(actor.user_id)
        ]

    async def revoke_session(self, actor: Actor, session_id: UUID) -> bool:
        token = await self._tokens.get_by_id(session_id)
        if not token or token.user_id != actor.user_id or token.revoked:
            raise UserSessionNotFoundError("session not found")
        await self._revoke(token)
        await self._session.commit()
        return token.access_jti == actor.jti

    async def change_password(
        self, actor: Actor, current: str, new: str, client: ClientInfo
    ) -> TokenPair:
        user = await self._users.get_by_id(actor.user_id)
        if user.role == UserRole.ROOT:
            raise InvalidCredentialsError("root password is set by ROOT_PASSWORD")
        if not await verify_password(current, user.password_hash):
            raise InvalidCredentialsError("wrong current password")
        user.password_hash = await hash_password(new)
        await self.revoke_all(user.id)
        pair = await self._issue(user, client)
        await self._session.commit()
        logger.info("User {} changed the password", user.id)
        return pair

    async def actor(self, user: UserDB, jti: UUID | None = None) -> Actor:
        grants = (
            []
            if user.role in (UserRole.ROOT, UserRole.ADMIN)
            else [
                GrantServiceRead.from_db(grant)
                for grant in await self._grants.get_by_user(user.id)
            ]
        )
        return Actor(
            user_id=user.id,
            login=user.login,
            role=user.role,
            jti=jti,
            grants=grants,
            settings=SettingsServiceRead.model_validate(user.settings),
        )

    async def authenticate_token(self, access_token: str) -> Actor:
        payload = decode_access_token(access_token)
        if await self._blacklist.revoked(payload.jti):
            raise TokenInvalidError("access token is revoked")
        user = await self._users.get_by_id(payload.user_id)
        if not user:
            raise TokenInvalidError("user no longer exists")
        if user.blocked:
            raise UserBlockedError("user is blocked")
        return await self.actor(user, payload.jti)

    async def authenticate_key(self, raw_key: str) -> Actor:
        key = await self._keys.get_by_hash(hash_secret(raw_key))
        if not key:
            raise NotAuthenticatedError("invalid API key")
        user = await self._users.get_by_id(key.user_id)
        if user.blocked:
            raise UserBlockedError("user is blocked")
        now = datetime.now(UTC)
        if not key.last_used_at or now - key.last_used_at > KEY_TOUCH_INTERVAL:
            await self._keys.touch(key.id)
            await self._session.commit()
        return await self.actor(user)
