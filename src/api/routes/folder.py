from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from src.api.dependencies import get_actor
from src.api.schemas import (
    FolderCreate,
    FolderDeleteResponse,
    FolderListingResponse,
    FolderResponse,
)
from src.application.factory import get_service_folder
from src.application.schemas import Actor
from src.application.services import FolderService
from src.infrastructure.archive import MEDIA_TYPES
from src.infrastructure.enums import ArchiveFormat

router = APIRouter(prefix="/folders", tags=["Folders"])


@router.post(
    path="",
    name="folders:create",
    summary="Create a folder with all parents (existing ones are merged)",
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    data: FolderCreate,
    actor: Actor = Depends(get_actor),
    folder_service: FolderService = Depends(get_service_folder),
) -> FolderResponse:
    return await folder_service.create(actor, data.path)


@router.get(
    path="",
    name="folders:list",
    summary="Folder contents",
    status_code=status.HTTP_200_OK,
)
async def get_folder(
    path: str,
    actor: Actor = Depends(get_actor),
    folder_service: FolderService = Depends(get_service_folder),
) -> FolderListingResponse:
    return await folder_service.get_listing(actor, path)


@router.get(
    path="/archive",
    name="folders:archive",
    summary="Download the folder with everything readable inside as one archive",
    status_code=status.HTTP_200_OK,
    response_class=StreamingResponse,
)
async def download_folder(
    path: str,
    archive_format: ArchiveFormat = Query(ArchiveFormat.ZIP, alias="format"),
    actor: Actor = Depends(get_actor),
    folder_service: FolderService = Depends(get_service_folder),
) -> StreamingResponse:
    name, body = await folder_service.open_archive(actor, path, archive_format)
    return StreamingResponse(
        body,
        media_type=MEDIA_TYPES[archive_format],
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    path="",
    name="folders:delete",
    summary="Delete a folder with everything inside from Telegram and the database",
    status_code=status.HTTP_200_OK,
)
async def delete_folder(
    path: str,
    actor: Actor = Depends(get_actor),
    folder_service: FolderService = Depends(get_service_folder),
) -> FolderDeleteResponse:
    return await folder_service.delete(actor, path)
