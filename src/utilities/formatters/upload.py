from typing import Annotated, Literal

from pydantic import Field

TcloudPath = Annotated[
    str,
    Field(
        min_length=1, max_length=1024, description="tcloud/{storage}/{folders}/{name}"
    ),
]
FileSize = Annotated[int, Field(gt=0, description="file size in bytes")]
SendAs = Literal["media", "file"]
OnConflict = Literal["error", "replace", "keep_both", "skip"]
Priority = Literal["low", "normal", "high"]
ArchiveName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        pattern=r"^[^/\\\x00-\x1f]+$",
        description="archive name without extension",
    ),
]
