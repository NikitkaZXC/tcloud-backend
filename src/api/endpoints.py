from fastapi import APIRouter, Depends

from src.api.dependencies import get_actor, get_root
from src.api.routes import (
    auth_router,
    file_router,
    folder_router,
    settings_router,
    storage_router,
    system_router,
    telegram_router,
    upload_router,
    user_router,
)

routers: APIRouter = APIRouter(prefix="/api")

routers.include_router(router=auth_router)
routers.include_router(router=user_router)
routers.include_router(router=system_router, dependencies=[Depends(get_actor)])
routers.include_router(router=telegram_router, dependencies=[Depends(get_root)])
routers.include_router(router=settings_router)
routers.include_router(router=storage_router)
routers.include_router(router=folder_router)
routers.include_router(router=upload_router)
routers.include_router(router=file_router)
