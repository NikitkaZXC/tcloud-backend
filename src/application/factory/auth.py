from fastapi import Depends

from src.application.factory.runtime import get_runtime
from src.application.services import AuthService
from src.infrastructure.background import AppRuntime


def get_service_auth(runtime: AppRuntime = Depends(get_runtime)) -> AuthService:
    return AuthService(runtime=runtime)
