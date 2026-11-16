from src.utilities.errors import TCloudError


class NotAuthenticatedError(TCloudError):
    """Raised when a request has no valid access token or API key."""

    code = "unauthorized"


class ForbiddenError(TCloudError):
    """Raised when the user's role or folder grants do not allow the action."""

    code = "forbidden"


class InvalidCredentialsError(TCloudError):
    """Raised when the login or password is wrong."""

    code = "invalid_credentials"


class AccountLockedError(TCloudError):
    """Raised when sign-in is locked after too many wrong passwords."""

    code = "account_locked"


class RateLimitedError(TCloudError):
    """Raised when one client sends too many requests to a limited endpoint."""

    code = "rate_limited"


class UserBlockedError(TCloudError):
    """Raised when a blocked user signs in or uses a token or key."""

    code = "user_blocked"


class InvalidRefreshTokenError(TCloudError):
    """Raised when a refresh token is unknown, revoked or expired."""

    code = "invalid_refresh_token"


class UserNotFoundError(TCloudError):
    """Raised when a user does not exist."""

    code = "user_not_found"


class UserExistsError(TCloudError):
    """Raised when the login is already taken."""

    code = "user_exists"


class GrantNotFoundError(TCloudError):
    """Raised when a folder grant does not exist."""

    code = "grant_not_found"


class ApiKeyNotFoundError(TCloudError):
    """Raised when an API key does not exist."""

    code = "api_key_not_found"


class UserSessionNotFoundError(TCloudError):
    """Raised when a sign-in session of the user does not exist."""

    code = "user_session_not_found"
