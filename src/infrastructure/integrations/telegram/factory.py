from pathlib import Path

from src.config import settings
from src.infrastructure.enums import TelegramSession
from src.infrastructure.integrations.telegram.client import TelegramClient
from src.infrastructure.integrations.telegram.login import TelegramLogin, session_file


def session_path(target: TelegramSession) -> Path:
    if target == TelegramSession.UPLOAD:
        return settings.telegram.UPLOAD_SESSION
    if target == TelegramSession.DOWNLOAD:
        return settings.telegram.DOWNLOAD_SESSION
    return settings.telegram.SESSION


def session_exists(target: TelegramSession) -> bool:
    return session_file(session_path(target)).exists()


def create_telegram(
    target: TelegramSession = TelegramSession.API, receive_updates: bool = True
) -> TelegramClient:
    return TelegramClient(
        session_path(target),
        settings.telegram.API_ID,
        settings.telegram.API_HASH.get_secret_value(),
        settings.telegram.UPLOAD_WORKERS,
        receive_updates,
        target.value,
    )


def create_login(target: TelegramSession) -> TelegramLogin:
    return TelegramLogin(
        session_path(target),
        settings.telegram.API_ID,
        settings.telegram.API_HASH.get_secret_value(),
        target.value,
    )
