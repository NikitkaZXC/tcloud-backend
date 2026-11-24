from pydantic import BaseModel

from src.api.schemas.base import RequestBody
from src.application.schemas import StorageActivityServiceRead, StorageServiceRead
from src.utilities.formatters import StorageDescription, StorageName


class StorageCreate(RequestBody):
    name: StorageName
    description: StorageDescription | None = None


class StorageUpdate(RequestBody):
    name: StorageName | None = None
    description: StorageDescription | None = None


class StorageResponse(StorageServiceRead):
    pass


class StorageActivityResponse(StorageActivityServiceRead):
    pass
