from src.utilities.errors import TCloudError


class InfectedFileError(TCloudError):
    """Raised when the antivirus finds malware in a file."""

    code = "infected"


class UnscannableFileError(TCloudError):
    """Raised when the antivirus cannot look inside a file: encryption, macros or scan limits."""

    code = "unscannable"


class ScannerUnavailableError(TCloudError):
    """Raised when the antivirus cannot be reached; uploads are rejected until it is back."""

    code = "scanner_unavailable"
