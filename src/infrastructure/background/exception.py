from src.utilities.errors import TCloudError


class InsufficientStorageError(TCloudError):
    """Raised when temporary files would not fit on the disk."""

    code = "insufficient_storage"
