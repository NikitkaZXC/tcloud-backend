from src.utilities.errors import TCloudError


class AlreadyAuthorizedError(TCloudError):
    """Raised when logging in to a session that is already logged in."""

    code = "already_authorized"


class LoginNotFoundError(TCloudError):
    """Raised when there is no login in progress for a session."""

    code = "login_not_found"


class LoginStepError(TCloudError):
    """Raised when a login step is sent out of order."""

    code = "wrong_login_step"


class SessionNotFoundError(TCloudError):
    """Raised when a Telegram session is not one of the tcloud sessions."""

    code = "session_not_found"


class CurrentSessionError(TCloudError):
    """Raised when revoking the session the API itself runs on."""

    code = "current_session"


class SessionNotAuthorizedError(TCloudError):
    """Raised when a Telegram session needed for the request is not logged in."""

    code = "telegram_unauthorized"
