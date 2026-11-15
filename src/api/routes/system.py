from fastapi import APIRouter, Depends, status

from src.api.schemas import AccountResponse, HealthResponse
from src.application.factory import get_service_account, get_service_health
from src.application.services import AccountService, HealthService

router = APIRouter(tags=["System"])


@router.get(
    path="/health",
    name="system:health",
    summary="Database, Telegram and antivirus state",
    status_code=status.HTTP_200_OK,
)
async def health(
    health_service: HealthService = Depends(get_service_health),
) -> HealthResponse:
    return await health_service.check()


@router.get(
    path="/account",
    name="system:account",
    summary="Telegram Premium and the current file size limit",
    status_code=status.HTTP_200_OK,
)
async def account(
    account_service: AccountService = Depends(get_service_account),
) -> AccountResponse:
    return await account_service.get_account()
