from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from src.api.exception.errors import status_for
from src.utilities.errors import TCloudError


def error_response(status_code: int, body: dict) -> JSONResponse:
    return JSONResponse({"error": jsonable_encoder(body)}, status_code)


async def tcloud_error(_: Request, error: TCloudError) -> JSONResponse:
    return error_response(status_for(error.code), error.to_dict())


async def http_error(_: Request, error: HTTPException) -> JSONResponse:
    return error_response(
        error.status_code, {"code": "http_error", "message": str(error.detail)}
    )


async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    return error_response(
        422,
        {
            "code": "validation_error",
            "message": "invalid request",
            "details": error.errors(),
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(TCloudError, tcloud_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
