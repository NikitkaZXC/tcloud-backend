from pydantic import BaseModel, Field

from src.api.schemas.base import RequestBody
from src.application.schemas import (
    ApiKeyServiceRead,
    GrantServiceRead,
    SettingsServiceRead,
    UserServiceRead,
    UserSessionServiceRead,
)
from src.utilities.formatters import (
    ApiKeyName,
    ManagedRole,
    Password,
    TcloudPath,
    UserLogin,
    UserPassword,
)


class SignIn(RequestBody):
    login: str = Field(min_length=1, max_length=64)
    password: Password


class PasswordChange(RequestBody):
    current_password: Password
    new_password: UserPassword


class UserCreate(RequestBody):
    login: UserLogin
    password: UserPassword
    role: ManagedRole = "user"


class UserUpdate(RequestBody):
    role: ManagedRole | None = None
    blocked: bool | None = None
    password: UserPassword | None = None


class GrantCreate(RequestBody):
    path: TcloudPath
    recursive: bool = True
    can_read: bool = True
    can_upload: bool = False
    can_delete: bool = False


class ApiKeyCreate(RequestBody):
    name: ApiKeyName


class UserResponse(UserServiceRead):
    pass


class MeResponse(UserServiceRead):
    grants: list[GrantServiceRead]
    settings: SettingsServiceRead


class UserSessionResponse(UserSessionServiceRead):
    pass


class GrantResponse(GrantServiceRead):
    pass


class ApiKeyResponse(ApiKeyServiceRead):
    pass
