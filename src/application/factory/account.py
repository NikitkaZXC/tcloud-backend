from fastapi import Depends

from src.application.factory.runtime import get_runtime
from src.application.services import AccountService
from src.infrastructure.background import BackgroundRuntime


def get_service_account(
    runtime: BackgroundRuntime = Depends(get_runtime),
) -> AccountService:
    return AccountService(runtime=runtime)
