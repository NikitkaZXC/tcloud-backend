from pydantic import BaseModel


class AccountServiceRead(BaseModel):
    telegram_connected: bool
    premium: bool | None
    file_limit: int | None
    channels_used: int | None
    channels_limit: int | None


class HealthDetailServiceRead(BaseModel):
    latency_ms: float | None = None
    last_seen_s: float | None = None


class HealthServiceRead(BaseModel):
    ok: bool
    db: bool
    telegram: bool
    telegram_upload: bool
    telegram_download: bool
    antivirus: bool
    redis: bool
    queue: bool
    workers: dict[str, bool]
    details: dict[str, HealthDetailServiceRead]
