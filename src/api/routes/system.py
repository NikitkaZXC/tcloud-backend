from fastapi import APIRouter, Depends, Response, status

from src.api.dependencies import get_actor, get_optional_actor
from src.api.schemas import AccountResponse, HealthResponse, PublicHealthResponse
from src.application.factory import get_service_account, get_service_health
from src.application.schemas import Actor
from src.application.services import AccountService, HealthService

router = APIRouter(tags=["System"])


@router.get(
    path="/health",
    name="system:health",
    summary="Database, Telegram and antivirus state; without sign-in only ok or degraded",
    status_code=status.HTTP_200_OK,
    responses={503: {"model": PublicHealthResponse}},
)
async def health(
    response: Response,
    actor: Actor | None = Depends(get_optional_actor),
    health_service: HealthService = Depends(get_service_health),
) -> HealthResponse | PublicHealthResponse:
    report = await health_service.check()
    if actor:
        return report
    if not report.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return PublicHealthResponse(status="ok" if report.ok else "degraded")


@router.get(
    path="/account",
    name="system:account",
    summary="Telegram Premium and the current file size limit",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(get_actor)],
)
async def account(
    account_service: AccountService = Depends(get_service_account),
) -> AccountResponse:
    return await account_service.get_account()
