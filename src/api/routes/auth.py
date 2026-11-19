from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Request, Response, status

from src.api.dependencies import (
    RateLimiter,
    get_actor,
    get_rate_limiter,
    rate_limit_api,
    rate_limit_by_ip,
)
from src.api.schemas import PasswordChange, SignIn, UserSessionResponse
from src.application.exception import InvalidRefreshTokenError
from src.application.factory import get_service_user_auth
from src.application.schemas import Actor, ClientInfo, TokenPair
from src.application.services import UserAuthService
from src.config import settings

router = APIRouter(prefix="/auth", tags=["Auth"])

USER_AGENT_MAX_LENGTH = 1024


def client_info(request: Request) -> ClientInfo:
    user_agent = (request.headers.get("user-agent") or "").strip()
    return ClientInfo(
        user_agent=user_agent[:USER_AGENT_MAX_LENGTH] or None,
        ip_address=request.client.host if request.client else None,
    )


def _set_cookie(response: Response, key: str, value: str, max_age: int) -> None:
    response.set_cookie(
        key=key,
        value=value,
        max_age=max_age,
        path=settings.jwt.COOKIE_PATH,
        domain=settings.jwt.COOKIE_DOMAIN,
        secure=settings.jwt.SECURE,
        httponly=True,
        samesite=settings.jwt.SAME_SITE,
    )


def set_auth_cookies(response: Response, pair: TokenPair) -> None:
    _set_cookie(
        response,
        settings.jwt.ACCESS_TOKEN_TYPE,
        pair.access_token,
        settings.jwt.ACCESS_TOKEN_EXPIRATION_MINUTES * 60,
    )
    _set_cookie(
        response,
        settings.jwt.REFRESH_TOKEN_TYPE,
        pair.refresh_token,
        settings.jwt.REFRESH_TOKEN_EXPIRATION_MINUTES * 60,
    )


def delete_auth_cookies(response: Response) -> None:
    for key in (settings.jwt.ACCESS_TOKEN_TYPE, settings.jwt.REFRESH_TOKEN_TYPE):
        response.delete_cookie(
            key=key,
            path=settings.jwt.COOKIE_PATH,
            domain=settings.jwt.COOKIE_DOMAIN,
            secure=settings.jwt.SECURE,
            httponly=True,
            samesite=settings.jwt.SAME_SITE,
        )


@router.post(
    path="/signin",
    dependencies=[Depends(rate_limit_by_ip("signin", settings.rate_limit.SIGNIN_IP))],
    name="auth:signin",
    summary="Sign in with login and password and get auth cookies",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def signin(
    data: SignIn,
    request: Request,
    response: Response,
    limiter: RateLimiter = Depends(get_rate_limiter),
    auth_service: UserAuthService = Depends(get_service_user_auth),
) -> None:
    await limiter.hit(
        "signin_login", data.login.lower(), settings.rate_limit.SIGNIN_LOGIN
    )
    pair = await auth_service.signin(
        data.login, data.password.get_secret_value(), client_info(request)
    )
    set_auth_cookies(response, pair)


@router.post(
    path="/refresh",
    dependencies=[Depends(rate_limit_by_ip("refresh", settings.rate_limit.REFRESH_IP))],
    name="auth:refresh",
    summary="Rotate auth cookies with the refresh token",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def refresh(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(
        default=None, alias=settings.jwt.REFRESH_TOKEN_TYPE
    ),
    auth_service: UserAuthService = Depends(get_service_user_auth),
) -> None:
    if not refresh_token:
        raise InvalidRefreshTokenError("refresh token cookie is missing")
    set_auth_cookies(
        response, await auth_service.refresh(refresh_token, client_info(request))
    )


@router.post(
    path="/logout",
    dependencies=[Depends(rate_limit_by_ip("logout", settings.rate_limit.REFRESH_IP))],
    name="auth:logout",
    summary="Log out of the current session",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(
        default=None, alias=settings.jwt.REFRESH_TOKEN_TYPE
    ),
    auth_service: UserAuthService = Depends(get_service_user_auth),
) -> None:
    delete_auth_cookies(response)
    if refresh_token:
        await auth_service.logout(refresh_token)


@router.post(
    path="/logout/all",
    dependencies=[Depends(rate_limit_api)],
    name="auth:logout-all",
    summary="Log out of all sessions",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout_all(
    response: Response,
    actor: Actor = Depends(get_actor),
    auth_service: UserAuthService = Depends(get_service_user_auth),
) -> None:
    await auth_service.logout_all(actor)
    delete_auth_cookies(response)


@router.get(
    path="/sessions",
    dependencies=[Depends(rate_limit_api)],
    name="auth:sessions",
    summary="Own active sessions",
    status_code=status.HTTP_200_OK,
)
async def get_sessions(
    actor: Actor = Depends(get_actor),
    auth_service: UserAuthService = Depends(get_service_user_auth),
) -> list[UserSessionResponse]:
    return await auth_service.sessions(actor)


@router.delete(
    path="/sessions/{session_id}",
    dependencies=[Depends(rate_limit_api)],
    name="auth:session-revoke",
    summary="End one of own sessions",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_session(
    session_id: UUID,
    response: Response,
    actor: Actor = Depends(get_actor),
    auth_service: UserAuthService = Depends(get_service_user_auth),
) -> None:
    if await auth_service.revoke_session(actor, session_id):
        delete_auth_cookies(response)


@router.post(
    path="/password",
    dependencies=[Depends(rate_limit_api)],
    name="auth:password",
    summary="Change own password: other sessions are logged out",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def change_password(
    data: PasswordChange,
    request: Request,
    response: Response,
    actor: Actor = Depends(get_actor),
    auth_service: UserAuthService = Depends(get_service_user_auth),
) -> None:
    pair = await auth_service.change_password(
        actor,
        data.current_password.get_secret_value(),
        data.new_password.get_secret_value(),
        client_info(request),
    )
    set_auth_cookies(response, pair)
