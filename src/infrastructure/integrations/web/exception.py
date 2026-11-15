from src.utilities.errors import TCloudError


class UnsafeUrlError(TCloudError):
    """Raised when a URL points to a private address, uses a bad scheme or redirects too much."""

    code = "unsafe_url"


class DownloadFailedError(TCloudError):
    """Raised when the remote server does not return the file."""

    code = "download_failed"


class DownloadTooLargeError(TCloudError):
    """Raised when the remote file is larger than the account limit."""

    code = "too_large"
