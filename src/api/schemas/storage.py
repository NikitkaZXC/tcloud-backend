from pydantic import BaseModel

from src.application.schemas import StorageServiceRead
from src.utilities.formatters import StorageDescription, StorageName


class StorageCreate(BaseModel):
    name: StorageName
    description: StorageDescription | None = None


class StorageUpdate(BaseModel):
    name: StorageName | None = None
    description: StorageDescription | None = None


class StorageResponse(StorageServiceRead):
    pass
