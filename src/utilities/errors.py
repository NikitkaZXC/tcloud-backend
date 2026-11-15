class TCloudError(Exception):
    """Base error with a stable machine-readable code returned to API clients."""

    code = "internal"

    def __init__(self, message: str = "", **extra: object) -> None:
        super().__init__(message or self.code)
        self.extra = extra

    @property
    def message(self) -> str:
        return str(self)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}
