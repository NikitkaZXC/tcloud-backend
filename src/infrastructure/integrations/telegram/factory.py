from src.config import settings
from src.infrastructure.integrations.telegram.client import TelegramClient


def create_telegram() -> TelegramClient:
    return TelegramClient(
        settings.telegram.SESSION,
        settings.telegram.API_ID,
        settings.telegram.API_HASH.get_secret_value(),
        settings.telegram.UPLOAD_WORKERS,
    )
