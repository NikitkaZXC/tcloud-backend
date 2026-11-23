import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial

from loguru import logger

from src.application.exception import (
    AlreadyAuthorizedError,
    CurrentSessionError,
    LoginNotFoundError,
    LoginStepError,
    SessionNotFoundError,
)
from src.application.schemas import (
    AuthorizationServiceRead,
    AuthServiceRead,
    LoginServiceRead,
    QrServiceRead,
    SessionStateServiceRead,
)
from src.application.services.file import FileService
from src.application.services.storage import StorageService
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.session import session_factory
from src.infrastructure.enums import TelegramSession
from src.infrastructure.integrations import (
    Authorization,
    TelegramCodeInvalidError,
    TelegramLogin,
    TelegramPasswordInvalidError,
    TelegramPasswordNeededError,
    TelegramSessionTooFreshError,
    TelegramUnauthorizedError,
    session_exists,
)
from src.utilities.errors import TCloudError

MODELS = {session.value: session for session in TelegramSession}
MAX_ATTEMPTS = 5
KEEPALIVE_INTERVAL = 15


@dataclass
class Login:
    session: TelegramSession
    client: TelegramLogin
    step: str
    expires_at: datetime
    error: dict | None = None
    attempts: int = 0
    waiter: asyncio.Task | None = None


def _deadline() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=settings.telegram.LOGIN_TTL)


async def forget_deleted(
    runtime: AppRuntime, chat_id: int, message_ids: list[int]
) -> None:
    try:
        async with session_factory() as session:
            files = FileService(session, runtime, StorageService(session, runtime))
            await files.forget_messages(chat_id, message_ids)
    except Exception:
        logger.exception("Could not forget messages deleted in {}", chat_id)


class AuthService:
    def __init__(self, runtime: AppRuntime) -> None:
        self._runtime = runtime

    def authorized(self, session: TelegramSession) -> bool:
        if session == TelegramSession.API:
            return self._runtime.telegram.authorized
        if session == TelegramSession.DOWNLOAD:
            return self._runtime.download.authorized
        return session_exists(session)

    def _login(self, session: TelegramSession) -> Login | None:
        login = self._runtime.logins.get(session)
        if (
            login
            and login.step in ("starting", "qr", "code", "password")
            and datetime.now(UTC) > login.expires_at
        ):
            login.step = "expired"
            self._runtime.spawn(login.client.close())
        return login

    def state(self, session: TelegramSession) -> SessionStateServiceRead:
        login = self._login(session)
        return SessionStateServiceRead(
            session=session,
            authorized=self.authorized(session),
            login=(
                LoginServiceRead(
                    step=login.step, expires_at=login.expires_at, error=login.error
                )
                if login
                else None
            ),
        )

    def status(self) -> AuthServiceRead:
        return AuthServiceRead(
            sessions=[self.state(session) for session in TelegramSession]
        )

    async def _drop(self, session: TelegramSession) -> None:
        login = self._runtime.logins.pop(session, None)
        if not login:
            return
        if login.waiter and login.waiter is not asyncio.current_task():
            login.waiter.cancel()
        await login.client.close()

    async def _begin(self, session: TelegramSession) -> Login:
        if self.authorized(session):
            raise AlreadyAuthorizedError(
                f"the {session} session is already logged in, log out first"
            )
        await self._drop(session)
        login = Login(
            session, self._runtime.make_login(session), "starting", _deadline()
        )
        self._runtime.logins[session] = login
        return login

    def _expect(self, session: TelegramSession, step: str) -> Login:
        login = self._login(session)
        if not login or login.step in ("expired", "failed"):
            raise LoginNotFoundError(
                f"no login in progress for the {session} session, start with qr or code"
            )
        if login.step != step:
            raise LoginStepError(f"the login waits for {login.step}, not {step}")
        return login

    async def _attempt(self, login: Login, call) -> None:
        try:
            await call
        except TelegramCodeInvalidError, TelegramPasswordInvalidError:
            login.attempts += 1
            if login.attempts >= MAX_ATTEMPTS:
                await self._drop(login.session)
            raise
        except TelegramPasswordNeededError:
            login.step, login.expires_at = "password", _deadline()
            return
        except BaseException:
            await self._drop(login.session)
            raise
        await self._complete(login)

    async def start_qr(self, session: TelegramSession) -> QrServiceRead:
        async with self._runtime.auth_lock:
            login = await self._begin(session)
            try:
                url, expires_at = await login.client.qr()
            except BaseException:
                await self._drop(session)
                raise
            login.step, login.expires_at = "qr", expires_at
            login.waiter = asyncio.create_task(self._wait_qr(login))
            logger.info("Telegram {} session: QR login started", session)
            return QrServiceRead(url=url, expires_at=expires_at)

    async def _wait_qr(self, login: Login) -> None:
        try:
            await login.client.wait_qr()
        except TelegramPasswordNeededError:
            login.step, login.expires_at = "password", _deadline()
            return
        except TCloudError as error:
            login.step, login.error = "failed", error.to_dict()
            await login.client.close()
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram {} QR login failed", login.session)
            login.step = "failed"
            login.error = {"code": "internal", "message": "internal error"}
            await login.client.close()
            return
        async with self._runtime.auth_lock:
            if self._runtime.logins.get(login.session) is not login:
                return
            try:
                await self._complete(login)
            except Exception as error:
                logger.exception("Telegram {} login failed", login.session)
                login.step = "failed"
                login.error = (
                    error.to_dict()
                    if isinstance(error, TCloudError)
                    else {"code": "internal", "message": "internal error"}
                )

    async def send_code(
        self, session: TelegramSession, phone: str
    ) -> SessionStateServiceRead:
        async with self._runtime.auth_lock:
            login = await self._begin(session)
            try:
                await login.client.send_code(phone)
            except BaseException:
                await self._drop(session)
                raise
            login.step, login.expires_at = "code", _deadline()
            logger.info("Telegram {} session: login code sent", session)
            return self.state(session)

    async def sign_in(
        self, session: TelegramSession, code: str
    ) -> SessionStateServiceRead:
        async with self._runtime.auth_lock:
            login = self._expect(session, "code")
            await self._attempt(login, login.client.sign_in(code))
            return self.state(session)

    async def password(
        self, session: TelegramSession, password: str
    ) -> SessionStateServiceRead:
        async with self._runtime.auth_lock:
            login = self._expect(session, "password")
            await self._attempt(login, login.client.password(password))
            return self.state(session)

    async def _complete(self, login: Login) -> None:
        await login.client.finish()
        self._runtime.logins.pop(login.session, None)
        logger.info("Telegram {} session logged in", login.session)
        if login.session == TelegramSession.API:
            await self._switch_api()
        elif login.session == TelegramSession.DOWNLOAD:
            await self._switch_download()
        await self.sweep_quietly()

    async def _switch_api(self) -> None:
        old, client = self._runtime.telegram, self._runtime.make_telegram()
        if not await client.start():
            raise TelegramUnauthorizedError("the new API session is not authorized")
        self._runtime.telegram = client
        await self._runtime.store.forget_account()
        if old is not client:
            await old.stop()
        await self.connect()

    async def _switch_download(self) -> None:
        old = self._runtime.download
        client = self._runtime.make_telegram(
            TelegramSession.DOWNLOAD, receive_updates=False
        )
        if not await client.start():
            raise TelegramUnauthorizedError(
                "the new download session is not authorized"
            )
        self._runtime.download = client
        if old is not client:
            await old.stop()

    async def connect(self) -> None:
        async with session_factory() as session:
            await StorageService(session, self._runtime).connect_chats(
                settings.telegram.CHAT_IDS
            )
        await self._runtime.telegram.watch_deletions(
            partial(forget_deleted, self._runtime)
        )

    async def logout(self, session: TelegramSession) -> SessionStateServiceRead:
        async with self._runtime.auth_lock:
            await self._drop(session)
            if session == TelegramSession.API:
                await self._runtime.telegram.log_out()
                client = self._runtime.make_telegram()
                await client.start()
                self._runtime.telegram = client
                await self._runtime.store.forget_account()
            elif session == TelegramSession.DOWNLOAD:
                await self._runtime.download.log_out()
                client = self._runtime.make_telegram(session, receive_updates=False)
                await client.start()
                self._runtime.download = client
            elif session_exists(session):
                client = self._runtime.make_telegram(session, receive_updates=False)
                await client.start()
                await client.log_out()
            logger.info("Telegram {} session logged out", session)
            return self.state(session)

    async def _tcloud(self) -> list[Authorization]:
        return [
            item
            for item in await self._runtime.telegram.authorizations()
            if item.device_model in MODELS and item.api_id == settings.telegram.API_ID
        ]

    async def sessions(self) -> list[AuthorizationServiceRead]:
        return [
            AuthorizationServiceRead(
                id=str(item.hash),
                session=MODELS[item.device_model],
                created_at=item.created_at,
                active_at=item.active_at,
                ip=item.ip,
                country=item.country,
                current=item.current,
            )
            for item in await self._tcloud()
        ]

    async def revoke(self, session_id: int) -> None:
        found = next(
            (item for item in await self._tcloud() if item.hash == session_id), None
        )
        if not found:
            raise SessionNotFoundError(f"tcloud session {session_id} not found")
        if found.current:
            raise CurrentSessionError(
                "this is the session the API runs on, log out of the api session instead"
            )
        await self._runtime.telegram.revoke(found.hash)
        logger.info("Telegram session {} revoked", found.device_model)

    async def sweep(self) -> int:
        newest: dict[str, Authorization] = {}
        stale = []
        items = sorted(
            await self._tcloud(),
            key=lambda item: (
                item.current,
                item.created_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        for item in items:
            if item.device_model in newest:
                stale.append(item)
            else:
                newest[item.device_model] = item
        for item in stale:
            await self._runtime.telegram.revoke(item.hash)
            logger.info(
                "Old Telegram session {} from {} revoked",
                item.device_model,
                item.created_at,
            )
        return len(stale)

    async def sweep_quietly(self) -> None:
        if not self._runtime.telegram.authorized:
            return
        try:
            await self.sweep()
        except TelegramSessionTooFreshError:
            logger.info(
                "Old Telegram sessions are kept until the API session is 24 h old"
            )
        except Exception:
            logger.exception("Telegram session sweep failed")

    async def close(self) -> None:
        for session in list(self._runtime.logins):
            await self._drop(session)

    async def run_keepalive(self) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            for client in (self._runtime.telegram, self._runtime.download):
                try:
                    await client.ensure_connected()
                except TCloudError:
                    pass

    async def run_sweeper(self) -> None:
        while True:
            await self.sweep_quietly()
            await asyncio.sleep(settings.telegram.SESSION_SWEEP_INTERVAL)
