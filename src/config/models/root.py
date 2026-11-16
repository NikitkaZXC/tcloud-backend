from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class RootSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ROOT_")

    LOGIN: str
    PASSWORD: SecretStr
