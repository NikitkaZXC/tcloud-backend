from dataclasses import dataclass


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
