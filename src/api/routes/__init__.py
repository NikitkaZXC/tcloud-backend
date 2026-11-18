from .auth import router as auth_router
from .file import router as file_router
from .folder import router as folder_router
from .settings import router as settings_router
from .storage import router as storage_router
from .system import router as system_router
from .telegram import router as telegram_router
from .upload import router as upload_router
from .user import router as user_router

__all__ = [
    "auth_router",
    "file_router",
    "folder_router",
    "settings_router",
    "storage_router",
    "system_router",
    "telegram_router",
    "upload_router",
    "user_router",
]
