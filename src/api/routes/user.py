from uuid import UUID

from fastapi import APIRouter, Depends, status

from src.api.dependencies import get_actor
from src.api.schemas import (
    ApiKeyCreate,
    ApiKeyResponse,
    GrantCreate,
    GrantResponse,
    MeResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)
from src.application.factory import (
    get_service_api_key,
    get_service_grant,
    get_service_user,
)
from src.application.schemas import (
    Actor,
    GrantServiceCreate,
    UserServiceCreate,
    UserServiceUpdate,
)
from src.application.services import ApiKeyService, GrantService, UserService

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    path="/me",
    name="users:me",
    summary="Current user with folder grants",
    status_code=status.HTTP_200_OK,
)
async def me(
    actor: Actor = Depends(get_actor),
    user_service: UserService = Depends(get_service_user),
) -> MeResponse:
    user = await user_service.me(actor)
    return MeResponse(**user.model_dump(), grants=actor.grants)


@router.get(
    path="",
    name="users:list",
    summary="All users (admin)",
    status_code=status.HTTP_200_OK,
)
async def get_users(
    actor: Actor = Depends(get_actor),
    user_service: UserService = Depends(get_service_user),
) -> list[UserResponse]:
    return await user_service.get_all(actor)


@router.post(
    path="",
    name="users:create",
    summary="Create a user (admin) or an admin (root)",
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    data: UserCreate,
    actor: Actor = Depends(get_actor),
    user_service: UserService = Depends(get_service_user),
) -> UserResponse:
    return await user_service.create(
        actor,
        UserServiceCreate(
            login=data.login,
            password=data.password.get_secret_value(),
            role=data.role,
        ),
    )


@router.get(
    path="/{user_id}",
    name="users:get",
    summary="User (admin)",
    status_code=status.HTTP_200_OK,
)
async def get_user(
    user_id: UUID,
    actor: Actor = Depends(get_actor),
    user_service: UserService = Depends(get_service_user),
) -> UserResponse:
    return await user_service.get(actor, user_id)


@router.patch(
    path="/{user_id}",
    name="users:update",
    summary="Change role, block or reset the password: sessions are logged out",
    status_code=status.HTTP_200_OK,
)
async def update_user(
    user_id: UUID,
    data: UserUpdate,
    actor: Actor = Depends(get_actor),
    user_service: UserService = Depends(get_service_user),
) -> UserResponse:
    return await user_service.update(
        actor,
        user_id,
        UserServiceUpdate(
            role=data.role,
            blocked=data.blocked,
            password=data.password.get_secret_value() if data.password else None,
        ),
    )


@router.delete(
    path="/{user_id}",
    name="users:delete",
    summary="Delete a user with grants, keys and sessions",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_user(
    user_id: UUID,
    actor: Actor = Depends(get_actor),
    user_service: UserService = Depends(get_service_user),
) -> None:
    await user_service.delete(actor, user_id)


@router.get(
    path="/{user_id}/grants",
    name="users:grants",
    summary="Folder grants of a user",
    status_code=status.HTTP_200_OK,
)
async def get_grants(
    user_id: UUID,
    actor: Actor = Depends(get_actor),
    grant_service: GrantService = Depends(get_service_grant),
) -> list[GrantResponse]:
    return await grant_service.get_all(actor, user_id)


@router.put(
    path="/{user_id}/grants",
    name="users:grant",
    summary="Grant access to a folder; a grant on the same folder is replaced",
    status_code=status.HTTP_200_OK,
)
async def put_grant(
    user_id: UUID,
    data: GrantCreate,
    actor: Actor = Depends(get_actor),
    grant_service: GrantService = Depends(get_service_grant),
) -> GrantResponse:
    return await grant_service.put(
        actor, user_id, GrantServiceCreate.model_validate(data.model_dump())
    )


@router.delete(
    path="/{user_id}/grants/{grant_id}",
    name="users:revoke-grant",
    summary="Take a folder grant away",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_grant(
    user_id: UUID,
    grant_id: UUID,
    actor: Actor = Depends(get_actor),
    grant_service: GrantService = Depends(get_service_grant),
) -> None:
    await grant_service.delete(actor, user_id, grant_id)


@router.get(
    path="/{user_id}/api-keys",
    name="users:api-keys",
    summary="API keys of a user (without the keys themselves)",
    status_code=status.HTTP_200_OK,
)
async def get_api_keys(
    user_id: UUID,
    actor: Actor = Depends(get_actor),
    api_key_service: ApiKeyService = Depends(get_service_api_key),
) -> list[ApiKeyResponse]:
    return await api_key_service.get_all(actor, user_id)


@router.post(
    path="/{user_id}/api-keys",
    name="users:create-api-key",
    summary="Issue an API key: it is shown only in this response",
    status_code=status.HTTP_201_CREATED,
)
async def create_api_key(
    user_id: UUID,
    data: ApiKeyCreate,
    actor: Actor = Depends(get_actor),
    api_key_service: ApiKeyService = Depends(get_service_api_key),
) -> ApiKeyResponse:
    return await api_key_service.create(actor, user_id, data.name)


@router.delete(
    path="/{user_id}/api-keys/{key_id}",
    name="users:revoke-api-key",
    summary="Revoke an API key",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_api_key(
    user_id: UUID,
    key_id: UUID,
    actor: Actor = Depends(get_actor),
    api_key_service: ApiKeyService = Depends(get_service_api_key),
) -> None:
    await api_key_service.delete(actor, user_id, key_id)
