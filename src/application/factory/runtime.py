from fastapi import Request

from src.infrastructure.background import BackgroundRuntime


def get_runtime(request: Request) -> BackgroundRuntime:
    return request.app.state.runtime
