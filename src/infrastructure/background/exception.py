from src.utilities.errors import TCloudError


class InsufficientStorageError(TCloudError):
    """Raised when temporary files would not fit on the disk."""

    code = "insufficient_storage"


class QueueUnavailableError(TCloudError):
    """Raised when the upload queue broker is not reachable."""

    code = "queue_unavailable"
