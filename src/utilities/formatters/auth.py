from typing import Annotated

from pydantic import Field, SecretStr

PhoneNumber = Annotated[
    str,
    Field(pattern=r"^\+?[0-9]{7,15}$", description="international format, +7999..."),
]
LoginCode = Annotated[str, Field(pattern=r"^[0-9]{3,10}$")]
Password = Annotated[SecretStr, Field(min_length=1, max_length=256)]
