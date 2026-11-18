from src.utilities.errors import TCloudError


class StoredFileNotFoundError(TCloudError):
    """Raised when a file does not exist."""

    code = "file_not_found"


class PathConflictError(TCloudError):
    """Raised when a file already exists at the target path."""

    code = "conflict"


class FileChangedError(TCloudError):
    """Raised when the file in Telegram no longer matches the recorded size."""

    code = "file_changed"


class ThumbnailNotFoundError(TCloudError):
    """Raised when neither Telegram nor the database has a thumbnail for the file."""

    code = "thumbnail_not_found"


class EmptySearchError(TCloudError):
    """Raised when a search has neither a query nor any filter."""

    code = "empty_search"
