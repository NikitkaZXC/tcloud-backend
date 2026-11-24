from src.utilities.errors import TCloudError


class FolderNotFoundError(TCloudError):
    """Raised when a folder does not exist."""

    code = "folder_not_found"


class InvalidCursorError(TCloudError):
    """Raised when a listing cursor is malformed or made for another sort."""

    code = "invalid_cursor"


class StorageRootError(TCloudError):
    """Raised when creating or deleting the storage root as a folder."""

    code = "invalid_path"


class EmptySelectionError(TCloudError):
    """Raised when an archive selection has no files and no folders."""

    code = "empty_selection"


class TooManyItemsError(TCloudError):
    """Raised when an archive selection has more items than allowed."""

    code = "too_many_items"


class StorageMismatchError(TCloudError):
    """Raised when selected files and folders belong to different storages."""

    code = "storage_mismatch"


class ArchiveInProgressError(TCloudError):
    """Raised when the user already has an archive task that is not finished."""

    code = "archive_in_progress"
