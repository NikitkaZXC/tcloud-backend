from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class RootSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROOT_")

    LOGIN: str = Field(min_length=1, max_length=64)
    PASSWORD: SecretStr = Field(min_length=12)
