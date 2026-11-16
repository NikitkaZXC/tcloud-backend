from fastapi import Request

from src.infrastructure.background import AppRuntime


def get_runtime(request: Request) -> AppRuntime:
    return request.app.state.runtime
