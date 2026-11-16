from src.utilities.errors import TCloudError


class StorageNotFoundError(TCloudError):
    """Raised when a storage does not exist."""

    code = "storage_not_found"


class StorageBusyError(TCloudError):
    """Raised when a storage has uploads in progress."""

    code = "storage_busy"


class StoragePhotoNotFoundError(TCloudError):
    """Raised when a storage channel has no photo."""

    code = "photo_not_found"
