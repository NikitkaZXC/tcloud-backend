from pathlib import Path

from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class UploadSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPLOAD_")

    TMP_DIR: Path = Path("/data/tmp")
    DOWNLOAD_CONCURRENCY: int = 4
    SCAN_CONCURRENCY: int = 4
    MAX_ACTIVE_TASKS: int = 500
    PENDING_TTL: int = 3600
    MIN_FREE_DISK_MB: int = 1024
    RETRIES: int = 3
    RETRY_DELAY: float = 2.0
    BATCH_MAX: int = 100
    WEBHOOK_INTERVAL: float = 2.0
    PREMIUM_TTL: float = 60.0
