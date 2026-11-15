from src.utilities.errors import TCloudError


class StorageNotFoundError(TCloudError):
    """Raised when a storage does not exist."""

    code = "storage_not_found"


class StoragePhotoNotFoundError(TCloudError):
    """Raised when a storage channel has no photo."""

    code = "photo_not_found"
