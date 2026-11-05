"""TCloud — Telegram как зашифрованное облачное хранилище с REST API."""

from .crypto import CryptoError
from .service import TCloudService

__version__ = "0.1.0"
__all__ = ["CryptoError", "TCloudService", "__version__"]
