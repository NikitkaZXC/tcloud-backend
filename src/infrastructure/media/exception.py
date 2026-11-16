from src.utilities.errors import TCloudError


class UnsupportedMediaError(TCloudError):
    """Raised when the file is not a supported photo, video, audio or GIF."""

    code = "unsupported_media_type"


class InvalidMediaError(TCloudError):
    """Raised when the file is damaged or only pretends to be media."""

    code = "invalid_media"


class ExtensionMismatchError(TCloudError):
    """Raised when the file name extension does not match its real content."""

    code = "extension_mismatch"


class BlockedFileError(TCloudError):
    """Raised when the file is an executable, a script or an installer."""

    code = "blocked_file_type"
