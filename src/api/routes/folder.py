from fastapi import APIRouter, Depends, Query, status

from src.api.dependencies import get_actor
from src.api.schemas import (
    FolderCreate,
    FolderDeleteResponse,
    FolderListingResponse,
    FolderResponse,
    SelectionArchiveCreate,
    TaskResponse,
)
from src.application.factory import get_service_folder
from src.application.schemas import Actor
from src.application.services import FolderService
from src.infrastructure.enums import ArchiveFormat, TaskPriority

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


@router.post(
    path="/archive",
    name="folders:archive",
    summary="Build an archive of the folder with everything readable inside in the background",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_archive(
    path: str,
    archive_format: ArchiveFormat | None = Query(None, alias="format"),
    priority: TaskPriority | None = None,
    actor: Actor = Depends(get_actor),
    folder_service: FolderService = Depends(get_service_folder),
) -> TaskResponse:
    return await folder_service.create_archive(actor, path, archive_format, priority)


@router.post(
    path="/archive/selection",
    name="folders:archive-selection",
    summary="Build one archive of selected files and folders of one storage in the background",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_selection_archive(
    data: SelectionArchiveCreate,
    actor: Actor = Depends(get_actor),
    folder_service: FolderService = Depends(get_service_folder),
) -> TaskResponse:
    return await folder_service.create_selection_archive(
        actor, data.files, data.folders, data.format, data.priority, data.name
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
