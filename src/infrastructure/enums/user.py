from enum import StrEnum


class UserRole(StrEnum):
    ROOT = "root"
    ADMIN = "admin"
    USER = "user"


class Permission(StrEnum):
    READ = "read"
    UPLOAD = "upload"
    DELETE = "delete"
