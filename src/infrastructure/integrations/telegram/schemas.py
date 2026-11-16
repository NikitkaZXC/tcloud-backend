from dataclasses import dataclass
from datetime import datetime


@dataclass
class ChannelInfo:
    title: str
    about: str
    has_photo: bool


@dataclass
class SentFile:
    message_id: int
    size: int
    mime: str | None
    width: int | None
    height: int | None


@dataclass
class Authorization:
    hash: int
    device_model: str
    created_at: datetime | None
    active_at: datetime | None
    ip: str
    country: str
    current: bool
