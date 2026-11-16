from enum import StrEnum


class TelegramSession(StrEnum):
    API = "api"
    WORKER = "worker"
    DOWNLOAD = "download"
