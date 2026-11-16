from src.utilities.errors import TCloudError


class TokenInvalidError(TCloudError):
    """Raised when a token is malformed, forged or revoked."""

    code = "token_invalid"


class TokenExpiredError(TCloudError):
    """Raised when an access token has expired."""

    code = "token_expired"
