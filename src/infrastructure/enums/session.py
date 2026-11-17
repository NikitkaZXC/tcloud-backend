from enum import StrEnum


class TelegramSession(StrEnum):
    API = "api"
    UPLOAD = "upload"
    DOWNLOAD = "download"
