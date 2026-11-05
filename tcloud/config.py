from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MiB = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TCLOUD_", extra="ignore")

    # Доступ к API: заголовок X-API-Key
    api_key: str
    # Мастер-ключ шифрования (base64, 32 байта). Потеряете его — потеряете все файлы.
    master_key: str

    # Telegram (my.telegram.org)
    tg_api_id: int
    tg_api_hash: str
    # Нужно что-то одно: токен бота или session string пользователя
    tg_bot_token: str | None = None
    tg_session_string: str | None = None
    # Куда складывать файлы: id приватного канала (-100...), @username или "me" (Избранное, только для юзера)
    tg_chat_id: str
    tg_proxy_url: str | None = None  # socks5://user:pass@host:port

    data_dir: Path = Path("data")
    # Объём открытых данных в одной части. Лимит Telegram — 2000 МиБ (4000 с Premium)
    part_size_mb: int = 1900
    tg_max_upload_mb: int = 2000
    segment_size_kb: int = 4096

    host: str = "127.0.0.1"
    port: int = 8000

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if bool(self.tg_bot_token) == bool(self.tg_session_string):
            raise ValueError("set exactly one of TCLOUD_TG_BOT_TOKEN or TCLOUD_TG_SESSION_STRING")
        if self.tg_bot_token and self.tg_chat_id == "me":
            raise ValueError('a bot has no "me" chat: use a private channel id where the bot is an admin')
        if self.part_size_mb >= self.tg_max_upload_mb:
            raise ValueError("TCLOUD_PART_SIZE_MB must be smaller than TCLOUD_TG_MAX_UPLOAD_MB (room for encryption overhead)")
        if self.segment_size_kb <= 0 or self.part_size_mb <= 0:
            raise ValueError("sizes must be positive")
        return self

    @property
    def chat_id(self) -> int | str:
        v = self.tg_chat_id.strip()
        return int(v) if v.lstrip("-").isdigit() else v

    @property
    def segment_size(self) -> int:
        return self.segment_size_kb * 1024

    @property
    def part_size(self) -> int:
        # Часть — целое число сегментов
        return (self.part_size_mb * MiB // self.segment_size) * self.segment_size

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tcloud.db"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"
