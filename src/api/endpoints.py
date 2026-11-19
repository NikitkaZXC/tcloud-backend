from fastapi import APIRouter, Depends

from src.api.dependencies import get_root, rate_limit_api
from src.api.routes import (
    auth_router,
    file_router,
    file_stream_router,
    folder_router,
    settings_router,
    storage_router,
    system_router,
    telegram_router,
    upload_router,
    upload_stream_router,
    user_router,
)

routers: APIRouter = APIRouter(prefix="/api")

limited = [Depends(rate_limit_api)]

routers.include_router(router=auth_router)
routers.include_router(router=user_router, dependencies=limited)
routers.include_router(router=system_router)
routers.include_router(
    router=telegram_router, dependencies=[Depends(get_root), *limited]
)
routers.include_router(router=settings_router, dependencies=limited)
routers.include_router(router=storage_router, dependencies=limited)
routers.include_router(router=folder_router, dependencies=limited)
routers.include_router(router=upload_router, dependencies=limited)
routers.include_router(router=upload_stream_router)
routers.include_router(router=file_router, dependencies=limited)
routers.include_router(router=file_stream_router)
