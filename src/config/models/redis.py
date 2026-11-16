from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")

    HOST: str = "redis"
    PORT: int = 6379
    PASSWORD: SecretStr = SecretStr("")
    DB: int = 0
    PREFIX: str = "tcloud"
