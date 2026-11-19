from pathlib import Path
from typing import Literal

from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class JWTSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JWT_")

    ALGORITHM: str = "RS256"
    PRIVATE_KEY_PATH: Path = Path("certs/jwt-private.pem")
    PUBLIC_KEY_PATH: Path = Path("certs/jwt-public.pem")
    ACCESS_TOKEN_EXPIRATION_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRATION_DAYS: int = 30
    ACCESS_TOKEN_TYPE: str = "access"
    REFRESH_TOKEN_TYPE: str = "refresh"
    SECURE: bool = True
    COOKIE_DOMAIN: str | None = None
    COOKIE_PATH: str = "/"
    SAME_SITE: Literal["lax", "strict", "none"] = "lax"
    MAX_FAILED_LOGINS: int = 5
    LOCK_MINUTES: int = 15

    @property
    def REFRESH_TOKEN_EXPIRATION_MINUTES(self) -> int:
        return self.REFRESH_TOKEN_EXPIRATION_DAYS * 24 * 60
