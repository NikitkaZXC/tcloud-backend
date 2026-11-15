from typing import Annotated

from pydantic import Field

StorageName = Annotated[str, Field(min_length=1, max_length=128)]
StorageDescription = Annotated[str, Field(max_length=255)]
