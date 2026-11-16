from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.infrastructure.enums import TelegramSession

LoginStep = Literal["starting", "qr", "code", "password", "expired", "failed"]


class LoginServiceRead(BaseModel):
    step: LoginStep
    expires_at: datetime | None
    error: dict | None = None


class SessionStateServiceRead(BaseModel):
    session: TelegramSession
    authorized: bool
    login: LoginServiceRead | None = None


class AuthServiceRead(BaseModel):
    sessions: list[SessionStateServiceRead]


class QrServiceRead(BaseModel):
    url: str
    expires_at: datetime


class AuthorizationServiceRead(BaseModel):
    id: str
    session: TelegramSession
    created_at: datetime | None
    active_at: datetime | None
    ip: str
    country: str
    current: bool
