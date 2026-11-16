from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_")

    TITLE: str = "tcloud"
    VERSION: str = "0.3.0"
    SECRET_KEY: SecretStr
    HOST: str = "0.0.0.0"
    PORT: int = 8000
