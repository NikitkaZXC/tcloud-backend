from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOG_")

    LEVEL: str = "INFO"
