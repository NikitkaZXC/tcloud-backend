from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class AntivirusSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLAMAV_")

    HOST: str = "clamav"
    PORT: int = 3310
    TIMEOUT: float = 1860
    MAX_FILE_SIZE: int = 2147483645
