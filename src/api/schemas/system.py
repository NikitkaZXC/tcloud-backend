from typing import Literal

from pydantic import BaseModel

from src.application.schemas import (
    AccountServiceRead,
    HealthServiceRead,
    WebhookSecretServiceRead,
)


class AccountResponse(AccountServiceRead):
    pass


class HealthResponse(HealthServiceRead):
    pass


class PublicHealthResponse(BaseModel):
    status: Literal["ok", "degraded"]


class WebhookSecretResponse(WebhookSecretServiceRead):
    pass
