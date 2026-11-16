from pathlib import Path

from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class UploadSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPLOAD_")

    TMP_DIR: Path = Path("data/tmp")
    MAX_ACTIVE_TASKS: int = 500
    MAX_FILES_ON_DISK: int = 10
    TASK_TTL: int = 7 * 24 * 3600
    LOCK_TTL: int = 24 * 3600
    HEARTBEAT_INTERVAL: float = 10.0
    STALE_AFTER: float = 120.0
    SWEEP_INTERVAL: float = 60.0
    ORPHAN_AGE: float = 3600.0
    MIN_FREE_DISK_MB: int = 1024
    RETRIES: int = 3
    RETRY_DELAY: float = 2.0
    BATCH_MAX: int = 100
    WEBHOOK_INTERVAL: float = 2.0
    PREMIUM_TTL: float = 60.0
