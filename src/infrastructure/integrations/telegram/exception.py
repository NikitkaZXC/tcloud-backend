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


class TelegramUnauthorizedError(TCloudError):
    """Raised when a Telegram session is not logged in or was revoked."""

    code = "telegram_unauthorized"


class TelegramPasswordNeededError(TCloudError):
    """Raised when the account asks for its two-step verification password."""

    code = "password_needed"


class TelegramCodeInvalidError(TCloudError):
    """Raised when the login code is wrong or expired."""

    code = "invalid_code"


class TelegramPasswordInvalidError(TCloudError):
    """Raised when the two-step verification password is wrong."""

    code = "invalid_password"


class TelegramPhoneInvalidError(TCloudError):
    """Raised when Telegram does not accept the phone number."""

    code = "invalid_phone"


class TelegramLoginExpiredError(TCloudError):
    """Raised when a login QR code expired before it was scanned."""

    code = "login_expired"


class TelegramSessionTooFreshError(TCloudError):
    """Raised when Telegram forbids a new session to end other sessions yet."""

    code = "session_too_fresh"
