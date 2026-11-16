from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from src.infrastructure.database.models import (
    ApiKeyDB,
    FolderGrantDB,
    RefreshTokenDB,
    UserDB,
)
from src.infrastructure.enums import UserRole
from src.utilities.parsers import join_path


class GrantServiceRead(BaseModel):
    id: UUID
    storage_id: UUID
    folder: str
    path: str
    recursive: bool
    can_read: bool
    can_upload: bool
    can_delete: bool

    @classmethod
    def from_db(cls, grant: FolderGrantDB) -> "GrantServiceRead":
        return cls(
            id=grant.id,
            storage_id=grant.storage_id,
            folder=grant.path,
            path=join_path(grant.storage_id, grant.path),
            recursive=grant.recursive,
            can_read=grant.can_read,
            can_upload=grant.can_upload,
            can_delete=grant.can_delete,
        )


class GrantServiceCreate(BaseModel):
    path: str
    recursive: bool = True
    can_read: bool = True
    can_upload: bool = False
    can_delete: bool = False


class Actor(BaseModel):
    user_id: UUID
    login: str
    role: UserRole
    jti: UUID | None = None
    grants: list[GrantServiceRead] = []

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.ROOT, UserRole.ADMIN)


class UserServiceRead(BaseModel):
    id: UUID
    login: str
    role: UserRole
    blocked: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_db(cls, user: UserDB) -> "UserServiceRead":
        return cls.model_validate(user, from_attributes=True)


class UserServiceCreate(BaseModel):
    login: str
    password: str
    role: UserRole = UserRole.USER


class UserServiceUpdate(BaseModel):
    role: UserRole | None = None
    blocked: bool | None = None
    password: str | None = None


class UserSessionServiceRead(BaseModel):
    id: UUID
    user_agent: str | None
    ip_address: str | None
    signed_in_at: datetime
    last_active_at: datetime
    expires_at: datetime
    is_current: bool

    @classmethod
    def from_db(
        cls, token: RefreshTokenDB, current_jti: UUID | None
    ) -> "UserSessionServiceRead":
        return cls(
            id=token.id,
            user_agent=token.user_agent,
            ip_address=token.ip_address,
            signed_in_at=token.signed_in_at,
            last_active_at=token.created_at,
            expires_at=token.expires_at,
            is_current=token.access_jti == current_jti,
        )


class ApiKeyServiceRead(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    last_used_at: datetime | None
    created_at: datetime
    key: str | None = None

    @classmethod
    def from_db(cls, key: ApiKeyDB, raw: str | None = None) -> "ApiKeyServiceRead":
        return cls(
            id=key.id,
            user_id=key.user_id,
            name=key.name,
            last_used_at=key.last_used_at,
            created_at=key.created_at,
            key=raw,
        )


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str


class ClientInfo(BaseModel):
    user_agent: str | None = None
    ip_address: str | None = None
