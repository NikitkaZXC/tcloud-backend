from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_")

    HOST: str
    DB: str = "tcloud"
    PORT: int = 5432
    USER: SecretStr
    PASSWORD: SecretStr
    IS_DB_ECHO_LOG: bool = False
    POOL_SIZE: int = 10
    POOL_OVERFLOW: int = 10
    TIMEOUT: int = 5
    POOL_PRE_PING: bool = True

    @property
    def ASYNC_URL(self) -> SecretStr:
        return SecretStr(
            f"postgresql+asyncpg://{self.USER.get_secret_value()}:"
            f"{self.PASSWORD.get_secret_value()}@{self.HOST}:{self.PORT}/{self.DB}"
        )
