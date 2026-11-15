from src.utilities.errors import TCloudError


class FolderNotFoundError(TCloudError):
    """Raised when a folder does not exist."""

    code = "folder_not_found"


class StorageRootError(TCloudError):
    """Raised when creating or deleting the storage root as a folder."""

    code = "invalid_path"
