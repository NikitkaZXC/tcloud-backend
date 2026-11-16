from src.utilities.errors import TCloudError


class TokenInvalidError(TCloudError):
    """Raised when a token is malformed, forged or revoked."""

    code = "token_invalid"


class TokenExpiredError(TCloudError):
    """Raised when an access token has expired."""

    code = "token_expired"


class EncryptionUnavailableError(TCloudError):
    """Raised when encryption is requested but APP_ENCRYPTION_KEY is not set."""

    code = "encryption_unavailable"


class DecryptionError(TCloudError):
    """Raised when an encrypted file cannot be authenticated with the server key."""

    code = "decryption_failed"
