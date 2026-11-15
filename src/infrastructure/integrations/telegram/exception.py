from src.utilities.errors import TCloudError


class TelegramError(TCloudError):
    """Raised when Telegram rejects a request."""

    code = "telegram_error"


class TelegramFloodWaitError(TCloudError):
    """Raised when Telegram asks to wait before the next request."""

    code = "flood_wait"

    def __init__(self, seconds: int) -> None:
        super().__init__(f"Telegram asks to wait {seconds} s", retry_after=seconds)
        self.seconds = seconds


class TelegramUnavailableError(TCloudError):
    """Raised when Telegram cannot be reached; the request may be retried."""

    code = "telegram_unavailable"


class TelegramMessageGoneError(TCloudError):
    """Raised when the Telegram message with a stored file no longer exists."""

    code = "file_not_found"


class ChannelCheckError(TCloudError):
    """Raised when a channel cannot be used as a private storage."""

    code = "channel_unusable"
