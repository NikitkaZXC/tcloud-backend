from fastapi import Depends, Security
from fastapi.security import APIKeyCookie, APIKeyHeader

from src.application.exception import NotAuthenticatedError
from src.application.factory import get_service_user_auth
from src.application.schemas import Actor
from src.application.services import UserAuthService
from src.application.services.access import require_root
from src.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
access_cookie = APIKeyCookie(
    name=settings.jwt.ACCESS_TOKEN_TYPE,
    scheme_name="AccessCookie",
    description="HttpOnly access token cookie set by `POST /api/auth/signin`.",
    auto_error=False,
)


async def get_actor(
    key: str | None = Security(api_key_header),
    access_token: str | None = Security(access_cookie),
    auth_service: UserAuthService = Depends(get_service_user_auth),
) -> Actor:
    if key:
        return await auth_service.authenticate_key(key)
    if access_token:
        return await auth_service.authenticate_token(access_token)
    raise NotAuthenticatedError("sign in with /api/auth/signin or pass X-API-Key")


async def get_root(actor: Actor = Depends(get_actor)) -> Actor:
    require_root(actor)
    return actor
