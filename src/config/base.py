import os

from pydantic_settings import BaseSettings as PydanticBaseSettings
from pydantic_settings import SettingsConfigDict


def env_file() -> str | None:
    return os.environ.get("ENV_FILE", ".env") or None


class BaseSettings(PydanticBaseSettings):
    model_config = SettingsConfigDict(env_file=env_file(), extra="ignore")
