from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status

from src.api.dependencies import get_actor
from src.api.schemas import (
    BatchCreate,
    BatchResponse,
    PriorityUpdate,
    TaskListResponse,
    TaskResponse,
    UploadParams,
    UrlUploadCreate,
)
from src.application.factory import get_service_task, get_service_upload
from src.application.schemas import Actor, UploadServiceCreate
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
    actor: Actor = Depends(get_actor),
    upload_service: UploadService = Depends(get_service_upload),
) -> TaskResponse:
    data = UploadServiceCreate(**params.model_dump(), size=content_length)
    return await upload_service.upload(actor, data, request.stream())


@router.post(
    path="/uploads/from-url",
    name="uploads:from-url",
    summary="Upload a file from a link under its own name",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_url_upload(
    data: UrlUploadCreate,
    actor: Actor = Depends(get_actor),
    upload_service: UploadService = Depends(get_service_upload),
) -> TaskResponse:
    return await upload_service.upload_url(
        actor, UploadServiceCreate.model_validate(data.model_dump())
    )


@router.post(
    path="/uploads/batch",
    name="uploads:batch",
    summary="Queue up to 100 uploads from links; an error in one item does not stop the others",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_batch(
    data: BatchCreate,
    actor: Actor = Depends(get_actor),
    upload_service: UploadService = Depends(get_service_upload),
) -> BatchResponse:
    items = [
        UploadServiceCreate.model_validate(item.model_dump()) for item in data.items
    ]
    return await upload_service.create_batch(actor, items)


@router.get(
    path="/tasks",
    name="tasks:batch",
    summary="Tasks of a batch with a summary",
    status_code=status.HTTP_200_OK,
)
async def get_batch_tasks(
    batch_id: UUID,
    actor: Actor = Depends(get_actor),
    task_service: TaskService = Depends(get_service_task),
) -> TaskListResponse:
    return await task_service.get_batch(actor, batch_id)


@router.get(
    path="/tasks/{task_id}",
    name="tasks:get",
    summary="Upload progress",
    status_code=status.HTTP_200_OK,
)
async def get_task(
    task_id: UUID,
    actor: Actor = Depends(get_actor),
    task_service: TaskService = Depends(get_service_task),
) -> TaskResponse:
    return await task_service.get(task_id, actor)


@router.post(
    path="/tasks/{task_id}/cancel",
    name="tasks:cancel",
    summary="Cancel a task: queued ones stop at once, running ones within seconds",
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_task(
    task_id: UUID,
    actor: Actor = Depends(get_actor),
    upload_service: UploadService = Depends(get_service_upload),
) -> TaskResponse:
    return await upload_service.cancel(actor, task_id)


@router.post(
    path="/tasks/{task_id}/priority",
    name="tasks:priority",
    summary="Change the priority of a task that waits in a queue",
    status_code=status.HTTP_200_OK,
)
async def set_task_priority(
    task_id: UUID,
    data: PriorityUpdate,
    actor: Actor = Depends(get_actor),
    upload_service: UploadService = Depends(get_service_upload),
) -> TaskResponse:
    return await upload_service.set_priority(actor, task_id, data.priority)


@router.post(
    path="/tasks/{task_id}/retry",
    name="tasks:retry",
    summary="Retry a failed or cancelled upload from a link",
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_task(
    task_id: UUID,
    actor: Actor = Depends(get_actor),
    upload_service: UploadService = Depends(get_service_upload),
) -> TaskResponse:
    return await upload_service.retry(actor, task_id)
