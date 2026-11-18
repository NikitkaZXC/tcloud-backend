from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from src.application.schemas import (
    FileSearchServiceRead,
    FileServiceRead,
    MimeCountServiceRead,
)
from src.infrastructure.enums import MediaKind
from src.utilities.formatters import TcloudPath

SEARCH_LIMIT_MAX = 200
SEARCH_OFFSET_MAX = 100_000

MimeType = Annotated[
    str,
    Field(
        max_length=127,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$",
    ),
]


class FileResponse(FileServiceRead):
    pass


class FileSearchParams(BaseModel):
    q: str | None = Field(None, max_length=255)
    path: TcloudPath | None = None
    kind: list[MediaKind] = Field([], max_length=len(MediaKind))
    mime: list[MimeType] = Field([], max_length=50)
    size_min: int | None = Field(None, ge=0)
    size_max: int | None = Field(None, ge=0)
    date_from: datetime | None = None
    date_to: datetime | None = None
    encrypted: bool | None = None
    sort: Literal["relevance", "name", "size", "date"] = "relevance"
    order: Literal["asc", "desc"] | None = None
    limit: int = Field(50, ge=1, le=SEARCH_LIMIT_MAX)
    offset: int = Field(0, ge=0, le=SEARCH_OFFSET_MAX)

    @model_validator(mode="after")
    def check_ranges(self) -> "FileSearchParams":
        if (
            self.size_min is not None
            and self.size_max is not None
            and self.size_min > self.size_max
        ):
            raise ValueError("size_min is greater than size_max")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from is later than date_to")
        return self


class FileSearchResponse(FileSearchServiceRead):
    pass


class MimeCountResponse(MimeCountServiceRead):
    pass
