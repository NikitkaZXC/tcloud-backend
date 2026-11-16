from typing import Annotated, Literal

from pydantic import Field, SecretStr

UserLogin = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9_.-]{3,64}$", description="3–64 of A-Z a-z 0-9 _ . -"),
]
UserPassword = Annotated[SecretStr, Field(min_length=8, max_length=256)]
ManagedRole = Literal["admin", "user"]
ApiKeyName = Annotated[str, Field(min_length=1, max_length=100)]
