from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from src.api.schemas import StorageCreate, StorageResponse, StorageUpdate
from src.application.factory import get_service_storage
from src.application.schemas import StorageServiceCreate, StorageServiceUpdate
from src.application.services import StorageService

router = APIRouter(prefix="/storages", tags=["Storages"])


@router.post(
    path="",
    name="storages:create",
    summary="Create a private Telegram channel as a storage",
    status_code=status.HTTP_201_CREATED,
)
async def create_storage(
    data: StorageCreate, storage_service: StorageService = Depends(get_service_storage)
) -> StorageResponse:
    return await storage_service.create(
        StorageServiceCreate.model_validate(data.model_dump())
    )


@router.get(
    path="",
    name="storages:list",
    summary="List storages",
    status_code=status.HTTP_200_OK,
)
async def get_storages(
    storage_service: StorageService = Depends(get_service_storage),
) -> list[StorageResponse]:
    return await storage_service.get_all()


@router.get(
    path="/{storage_id}",
    name="storages:get",
    summary="Storage with file count and total size",
    status_code=status.HTTP_200_OK,
)
async def get_storage(
    storage_id: UUID, storage_service: StorageService = Depends(get_service_storage)
) -> StorageResponse:
    return await storage_service.get(storage_id)


@router.patch(
    path="/{storage_id}",
    name="storages:update",
    summary="Rename a storage or change its description in Telegram",
    status_code=status.HTTP_200_OK,
)
async def update_storage(
    storage_id: UUID,
    data: StorageUpdate,
    storage_service: StorageService = Depends(get_service_storage),
) -> StorageResponse:
    update = StorageServiceUpdate.model_validate(data.model_dump(exclude_unset=True))
    return await storage_service.update(storage_id, update)


@router.delete(
    path="/{storage_id}",
    name="storages:delete",
    summary="Delete a storage with its files: the channel too, unless keep_channel",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_storage(
    storage_id: UUID,
    keep_channel: bool = False,
    storage_service: StorageService = Depends(get_service_storage),
) -> None:
    await storage_service.delete(storage_id, keep_channel)


@router.put(
    path="/{storage_id}/photo",
    name="storages:set-photo",
    summary="Set the channel photo (request body is the image)",
    status_code=status.HTTP_200_OK,
)
async def set_photo(
    storage_id: UUID,
    request: Request,
    storage_service: StorageService = Depends(get_service_storage),
) -> StorageResponse:
    return await storage_service.set_photo(storage_id, request.stream())


@router.delete(
    path="/{storage_id}/photo",
    name="storages:delete-photo",
    summary="Remove the channel photo",
    status_code=status.HTTP_200_OK,
)
async def delete_photo(
    storage_id: UUID, storage_service: StorageService = Depends(get_service_storage)
) -> StorageResponse:
    return await storage_service.delete_photo(storage_id)


@router.get(
    path="/{storage_id}/photo",
    name="storages:get-photo",
    summary="Channel photo streamed from Telegram",
    status_code=status.HTTP_200_OK,
    response_class=Response,
)
async def get_photo(
    storage_id: UUID, storage_service: StorageService = Depends(get_service_storage)
) -> Response:
    return Response(
        await storage_service.get_photo(storage_id), media_type="image/jpeg"
    )
