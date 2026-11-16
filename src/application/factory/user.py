from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.factory.runtime import get_runtime
from src.application.factory.storage import get_service_storage
from src.application.services import (
    ApiKeyService,
    GrantService,
    StorageService,
    UserAuthService,
    UserService,
)
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.session import get_session


def get_service_user_auth(
    session: AsyncSession = Depends(get_session),
    runtime: AppRuntime = Depends(get_runtime),
) -> UserAuthService:
    return UserAuthService(session=session, runtime=runtime)


def get_service_user(
    session: AsyncSession = Depends(get_session),
    auth_service: UserAuthService = Depends(get_service_user_auth),
) -> UserService:
    return UserService(session=session, auth_service=auth_service)


def get_service_grant(
    session: AsyncSession = Depends(get_session),
    user_service: UserService = Depends(get_service_user),
    storage_service: StorageService = Depends(get_service_storage),
) -> GrantService:
    return GrantService(
        session=session, user_service=user_service, storage_service=storage_service
    )


def get_service_api_key(
    session: AsyncSession = Depends(get_session),
    user_service: UserService = Depends(get_service_user),
) -> ApiKeyService:
    return ApiKeyService(session=session, user_service=user_service)
