from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    API_ID: int
    API_HASH: SecretStr
    SESSION: Path = Path("/data/session/tcloud")
    WORKER_SESSION: Path = Path("/data/session/tcloud-worker")
    CHATS: str = ""
    CONCURRENCY: int = 2
    UPLOAD_WORKERS: int = 4
    FLOOD_MAX_WAIT: int = 3600

    @field_validator("CHATS")
    @classmethod
    def _chats(cls, value: str) -> str:
        for chat in filter(None, (item.strip() for item in value.split(","))):
            if not chat.lstrip("-").isdigit():
                raise ValueError(
                    f"TELEGRAM_CHATS must contain channel ids (-100...), got {chat!r}"
                )
        return value

    @property
    def CHAT_IDS(self) -> list[int]:
        return [
            int(chat)
            for chat in (item.strip() for item in self.CHATS.split(","))
            if chat
        ]
