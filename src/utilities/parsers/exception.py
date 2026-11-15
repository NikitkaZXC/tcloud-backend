from src.utilities.errors import TCloudError


class InvalidPathError(TCloudError):
    """Raised when a tcloud path is malformed."""

    code = "invalid_path"


class BadRangeError(TCloudError):
    """Raised when a Range header cannot be satisfied."""

    code = "bad_range"
