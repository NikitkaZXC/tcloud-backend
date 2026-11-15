from fastapi import APIRouter, Depends, status

from src.api.schemas import (
    FolderCreate,
    FolderDeleteResponse,
    FolderListingResponse,
    FolderResponse,
)
from src.application.factory import get_service_folder
from src.application.services import FolderService

router = APIRouter(prefix="/folders", tags=["Folders"])


@router.post(
    path="",
    name="folders:create",
    summary="Create a folder with all parents (existing ones are merged)",
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    data: FolderCreate, folder_service: FolderService = Depends(get_service_folder)
) -> FolderResponse:
    return await folder_service.create(data.path)


@router.get(
    path="",
    name="folders:list",
    summary="Folder contents",
    status_code=status.HTTP_200_OK,
)
async def get_folder(
    path: str, folder_service: FolderService = Depends(get_service_folder)
) -> FolderListingResponse:
    return await folder_service.get_listing(path)


@router.delete(
    path="",
    name="folders:delete",
    summary="Delete a folder with everything inside from Telegram and the database",
    status_code=status.HTTP_200_OK,
)
async def delete_folder(
    path: str, folder_service: FolderService = Depends(get_service_folder)
) -> FolderDeleteResponse:
    return await folder_service.delete(path)
