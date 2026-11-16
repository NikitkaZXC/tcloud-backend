from pydantic import BaseModel


class AccountServiceRead(BaseModel):
    premium: bool
    file_limit: int


class HealthServiceRead(BaseModel):
    ok: bool
    db: bool
    telegram: bool
    telegram_worker: bool
    telegram_download: bool
    antivirus: bool
    redis: bool
    queue: bool
    workers: dict[str, bool]
