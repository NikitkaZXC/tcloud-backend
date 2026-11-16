from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class RabbitMQSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RABBITMQ_")

    HOST: str = "rabbitmq"
    PORT: int = 5672
    USER: SecretStr = SecretStr("tcloud")
    PASSWORD: SecretStr = SecretStr("")
    VHOST: str = "/"

    @property
    def URL(self) -> SecretStr:
        return SecretStr(
            f"amqp://{self.USER.get_secret_value()}:"
            f"{self.PASSWORD.get_secret_value()}@{self.HOST}:"
            f"{self.PORT}/{self.VHOST.lstrip('/')}"
        )
