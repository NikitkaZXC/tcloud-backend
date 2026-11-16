from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status

from src.api.schemas import (
    BatchCreate,
    BatchResponse,
    TaskListResponse,
    TaskResponse,
    UploadParams,
    UrlUploadCreate,
)
from src.application.factory import get_service_task, get_service_upload
from src.application.schemas import UploadServiceCreate
from src.application.services import TaskService, UploadService
from src.utilities.formatters import FileSize

router = APIRouter(tags=["Uploads"])


@router.post(
    path="/uploads",
    name="uploads:create",
    summary="Upload a file: parameters in the query, the file in the body",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_upload(
    params: Annotated[UploadParams, Query()],
    request: Request,
    content_length: Annotated[FileSize | None, Header()] = None,
    upload_service: UploadService = Depends(get_service_upload),
) -> TaskResponse:
    data = UploadServiceCreate(**params.model_dump(), size=content_length)
    return await upload_service.upload(data, request.stream())


@router.post(
    path="/uploads/from-url",
    name="uploads:from-url",
    summary="Upload a file from a link under its own name",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_url_upload(
    data: UrlUploadCreate, upload_service: UploadService = Depends(get_service_upload)
) -> TaskResponse:
    return await upload_service.upload_url(
        UploadServiceCreate.model_validate(data.model_dump())
    )


@router.post(
    path="/uploads/batch",
    name="uploads:batch",
    summary="Queue up to 100 uploads from links; an error in one item does not stop the others",
    status_code=status.HTTP_202_ACCEPTED,
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
    name="tasks:batch",
    summary="Tasks of a batch with a summary",
    status_code=status.HTTP_200_OK,
)
async def get_batch_tasks(
    batch_id: UUID, task_service: TaskService = Depends(get_service_task)
) -> TaskListResponse:
    return await task_service.get_batch(batch_id)


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
