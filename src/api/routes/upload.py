from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status

from src.api.schemas import (
    BatchCreate,
    BatchResponse,
    TaskListResponse,
    TaskResponse,
    TaskStatusFilter,
    UploadCreate,
    UrlUploadCreate,
)
from src.application.factory import get_service_task, get_service_upload
from src.application.schemas import UploadServiceCreate
from src.application.services import TaskService, UploadService

router = APIRouter(tags=["Uploads"])


@router.post(
    path="/uploads",
    name="uploads:create",
    summary="Create an upload task; send the file with PUT /uploads/{task_id}",
    status_code=status.HTTP_201_CREATED,
)
async def create_upload(
    data: UploadCreate, upload_service: UploadService = Depends(get_service_upload)
) -> TaskResponse:
    return await upload_service.create(
        UploadServiceCreate.model_validate(data.model_dump())
    )


@router.put(
    path="/uploads/{task_id}",
    name="uploads:send-file",
    summary="Send the file body of an upload task",
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_file(
    task_id: UUID,
    request: Request,
    upload_service: UploadService = Depends(get_service_upload),
) -> TaskResponse:
    return await upload_service.receive(task_id, request.stream())


@router.post(
    path="/uploads/from-url",
    name="uploads:from-url",
    summary="Upload a file from a link under its own name",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_url_upload(
    data: UrlUploadCreate, upload_service: UploadService = Depends(get_service_upload)
) -> TaskResponse:
    return await upload_service.create(
        UploadServiceCreate.model_validate(data.model_dump())
    )


@router.post(
    path="/uploads/batch",
    name="uploads:batch",
    summary="Create up to 100 uploads at once; an error in one item does not stop the others",
    status_code=status.HTTP_201_CREATED,
)
async def create_batch(
    data: BatchCreate, upload_service: UploadService = Depends(get_service_upload)
) -> BatchResponse:
    items = [
        UploadServiceCreate.model_validate(item.model_dump()) for item in data.items
    ]
    return await upload_service.create_batch(items)


@router.get(
    path="/tasks",
    name="tasks:list",
    summary="Upload tasks with a batch summary",
    status_code=status.HTTP_200_OK,
)
async def get_tasks(
    batch_id: UUID | None = None,
    task_status: TaskStatusFilter | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    task_service: TaskService = Depends(get_service_task),
) -> TaskListResponse:
    return await task_service.get_page(batch_id, task_status, limit, offset)


@router.get(
    path="/tasks/{task_id}",
    name="tasks:get",
    summary="Upload progress",
    status_code=status.HTTP_200_OK,
)
async def get_task(
    task_id: UUID, task_service: TaskService = Depends(get_service_task)
) -> TaskResponse:
    return await task_service.get(task_id)
