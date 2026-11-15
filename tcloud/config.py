from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TCLOUD_", extra="ignore")

    # Доступ к API: заголовок X-API-Key
    api_key: str
    database_url: str = "postgresql+asyncpg://tcloud:tcloud@postgres:5432/tcloud"

    # Telegram (my.telegram.org). Нужен пользовательский аккаунт: бот не может создавать каналы
    tg_api_id: int
    tg_api_hash: str
    tg_session: Path = Path("/data/session/tcloud")
    # Каналы, которые подключаются как хранилища при старте: id через запятую (-100...)
    chats: str = ""
    # Одновременных выгрузок в Telegram — больше рискованно для аккаунта
    tg_concurrency: int = 2

    clamd_host: str = "clamav"
    clamd_port: int = 3310

    tmp_dir: Path = Path("/data/tmp")
    host: str = "0.0.0.0"  # внутри контейнера; наружу порт публикуется только на 127.0.0.1
    port: int = 8000

    @field_validator("chats")
    @classmethod
    def _chats(cls, v: str) -> str:
        for c in filter(None, (x.strip() for x in v.split(","))):
            if not c.lstrip("-").isdigit():
                raise ValueError(f"TCLOUD_CHATS must contain channel ids (-100...), got {c!r}")
        return v

    @property
    def chat_ids(self) -> list[int]:
        return [int(c) for c in (x.strip() for x in self.chats.split(",")) if c]
