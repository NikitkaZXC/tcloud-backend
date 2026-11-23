from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from src.api.exception.errors import status_for
from src.utilities.errors import TCloudError


def error_response(status_code: int, body: dict) -> JSONResponse:
    headers = (
        {"Retry-After": str(body["retry_after"])} if "retry_after" in body else None
    )
    return JSONResponse({"error": jsonable_encoder(body)}, status_code, headers)


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


async def client_disconnect(request: Request, _: ClientDisconnect) -> JSONResponse:
    logger.info("Client disconnected during {} {}", request.method, request.url.path)
    return error_response(
        400, {"code": "client_disconnected", "message": "the client went away"}
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ClientDisconnect, client_disconnect)
    app.add_exception_handler(TCloudError, tcloud_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
