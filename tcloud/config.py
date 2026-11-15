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
    # Параллельных кусков одного файла при выгрузке (ускоряет большие файлы)
    tg_upload_workers: int = 4
    # FloodWait дольше этого (с) — задача падает; короче — вся выгрузка ставится на паузу и продолжается
    tg_flood_max_wait: int = 3600

    # Сотни загрузок разом: сколько задач одновременно на каждой стадии
    download_concurrency: int = 4
    scan_concurrency: int = 4
    max_active_tasks: int = 500
    pending_ttl: int = 3600  # с: задача без тела файла дольше этого — failed
    min_free_disk_mb: int = 1024  # не принимать файлы, если на диске останется меньше
    retries: int = 3  # повторы при сетевых сбоях Telegram и недоступном ClamAV
    retry_delay: float = 2.0  # с, растёт: 2, 4, 8

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
