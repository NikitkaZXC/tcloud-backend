import secrets
from datetime import UTC, datetime, timedelta
from functools import cache
from uuid import UUID, uuid7

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from pydantic import BaseModel

from src.config import settings
from src.infrastructure.security.exception import TokenExpiredError, TokenInvalidError


class JWTPayload(BaseModel):
    sub: str
    user_id: UUID
    role: str
    jti: UUID
    iat: int
    exp: int
    token_type: str


@cache
def _private_key() -> str:
    return settings.jwt.PRIVATE_KEY_PATH.read_text()


@cache
def _public_key() -> str:
    return settings.jwt.PUBLIC_KEY_PATH.read_text()


def generate_access_token(user_id: UUID, role: str) -> tuple[str, JWTPayload]:
    now = datetime.now(UTC)
    payload = JWTPayload(
        sub=str(user_id),
        user_id=user_id,
        role=role,
        jti=uuid7(),
        iat=int(now.timestamp()),
        exp=int(
            (
                now + timedelta(minutes=settings.jwt.ACCESS_TOKEN_EXPIRATION_MINUTES)
            ).timestamp()
        ),
        token_type=settings.jwt.ACCESS_TOKEN_TYPE,
    )
    token = jwt.encode(
        payload.model_dump(mode="json"), _private_key(), settings.jwt.ALGORITHM
    )
    return token, payload


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def decode_access_token(token: str) -> JWTPayload:
    try:
        decoded = jwt.decode(
            token,
            _public_key(),
            algorithms=[settings.jwt.ALGORITHM],
            options={"require": ["sub", "jti", "iat", "exp", "token_type"]},
        )
        payload = JWTPayload.model_validate(decoded)
    except ExpiredSignatureError:
        raise TokenExpiredError("access token expired") from None
    except InvalidTokenError, ValueError:
        raise TokenInvalidError("invalid access token") from None
    if payload.token_type != settings.jwt.ACCESS_TOKEN_TYPE:
        raise TokenInvalidError("invalid access token")
    return payload
