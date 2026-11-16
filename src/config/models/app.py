from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from src.config.base import BaseSettings


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_")

    TITLE: str = "tcloud"
    VERSION: str = "0.3.0"
    SECRET_KEY: SecretStr
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DOCS_URL: str = "/docs"
    OPENAPI_URL: str = "/openapi.json"
    REDOC_URL: str = "/redoc"
    IS_ALLOWED_CREDENTIALS: bool = True
    ALLOWED_ORIGINS: list[str] = []
    ALLOWED_METHODS: list[str] = ["*"]
    ALLOWED_HEADERS: list[str] = ["*"]
    EXPOSED_HEADERS: list[str] = [
        "Accept-Ranges",
        "Content-Disposition",
        "Content-Range",
        "Retry-After",
        "X-Expires-At",
    ]

    @property
    def docs_paths(self) -> tuple[str, ...]:
        return (
            self.DOCS_URL,
            f"{self.DOCS_URL}/oauth2-redirect",
            self.REDOC_URL,
            self.OPENAPI_URL,
        )

    @property
    def set_app_attributes(self) -> dict:
        return {
            "title": self.TITLE,
            "version": self.VERSION,
            "docs_url": self.DOCS_URL,
            "openapi_url": self.OPENAPI_URL,
            "redoc_url": self.REDOC_URL,
            "swagger_ui_oauth2_redirect_url": f"{self.DOCS_URL}/oauth2-redirect",
        }
