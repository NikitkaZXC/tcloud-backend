from fastapi import APIRouter, Depends

from src.api.dependencies import verify_api_key
from src.api.routes import (
    file_router,
    folder_router,
    storage_router,
    system_router,
    upload_router,
)

routers: APIRouter = APIRouter(prefix="/api", dependencies=[Depends(verify_api_key)])

routers.include_router(router=system_router)
routers.include_router(router=storage_router)
routers.include_router(router=folder_router)
routers.include_router(router=upload_router)
routers.include_router(router=file_router)
