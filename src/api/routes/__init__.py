from .file import router as file_router
from .folder import router as folder_router
from .storage import router as storage_router
from .system import router as system_router
from .upload import router as upload_router

__all__ = [
    "file_router",
    "folder_router",
    "storage_router",
    "system_router",
    "upload_router",
]
