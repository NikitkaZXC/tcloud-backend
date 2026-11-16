from pydantic import BaseModel

from src.application.schemas import (
    ApiKeyServiceRead,
    GrantServiceRead,
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


class SignIn(BaseModel):
    login: str
    password: Password


class PasswordChange(BaseModel):
    current_password: Password
    new_password: UserPassword


class UserCreate(BaseModel):
    login: UserLogin
    password: UserPassword
    role: ManagedRole = "user"


class UserUpdate(BaseModel):
    role: ManagedRole | None = None
    blocked: bool | None = None
    password: UserPassword | None = None


class GrantCreate(BaseModel):
    path: TcloudPath
    recursive: bool = True
    can_read: bool = True
    can_upload: bool = False
    can_delete: bool = False


class ApiKeyCreate(BaseModel):
    name: ApiKeyName


class UserResponse(UserServiceRead):
    pass


class MeResponse(UserServiceRead):
    grants: list[GrantServiceRead]


class UserSessionResponse(UserSessionServiceRead):
    pass


class GrantResponse(GrantServiceRead):
    pass


class ApiKeyResponse(ApiKeyServiceRead):
    pass
