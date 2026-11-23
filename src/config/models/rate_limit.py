from pydantic import BaseModel
from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class RateLimitRule(BaseModel):
    limit: int
    window: int


class RateLimitSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RATE_LIMIT_", env_nested_delimiter="__"
    )

    ENABLED: bool = True
    PREFIX: str = "rate_limit"

    SIGNIN_IP: RateLimitRule = RateLimitRule(limit=20, window=60)
    SIGNIN_LOGIN: RateLimitRule = RateLimitRule(limit=10, window=900)
    REFRESH_IP: RateLimitRule = RateLimitRule(limit=60, window=60)
    HEALTH_IP: RateLimitRule = RateLimitRule(limit=120, window=60)
    TELEGRAM_LOGIN_USER: RateLimitRule = RateLimitRule(limit=10, window=600)
    UPLOAD_USER: RateLimitRule = RateLimitRule(limit=300, window=60)
    UPLOAD_BATCH_USER: RateLimitRule = RateLimitRule(limit=30, window=60)
    ARCHIVE_USER: RateLimitRule = RateLimitRule(limit=30, window=3600)
    PREVIEWS_BACKFILL_USER: RateLimitRule = RateLimitRule(limit=3, window=3600)
    PREVIEWS_CLEAR_USER: RateLimitRule = RateLimitRule(limit=3, window=3600)
    FILE_READ_USER: RateLimitRule = RateLimitRule(limit=3000, window=60)
    API_USER: RateLimitRule = RateLimitRule(limit=1200, window=60)
