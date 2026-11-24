import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from src.api.endpoints import routers
from src.api.exception import register_exception_handlers
from src.api.middleware import (
    BodyLimitMiddleware,
    OriginCheckMiddleware,
    SecurityHeadersMiddleware,
)
from src.application.bootstrap.startup import prepare
from src.application.services import AuthService, TaskSweeper
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.cache import (
    RateLimitStore,
    TaskStore,
    TokenBlacklist,
    create_redis,
)
from src.infrastructure.database.engine import create_engine
from src.infrastructure.database.session import session_factory
from src.infrastructure.enums import TelegramSession
from src.infrastructure.integrations import (
    create_login,
    create_scanner,
    create_telegram,
)
from src.infrastructure.logger import setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    logger.info("Starting {} v{}", settings.app.TITLE, settings.app.VERSION)
    engine = create_engine()
    session_factory.configure(bind=engine)
    telegram = create_telegram()
    await telegram.start()
    download = create_telegram(TelegramSession.DOWNLOAD, receive_updates=False)
    await download.start()
    redis = create_redis()
    runtime = AppRuntime(
        telegram=telegram,
        download=download,
        scanner=create_scanner(),
        store=TaskStore(redis),
        blacklist=TokenBlacklist(redis),
        rate_limits=RateLimitStore(redis),
        make_telegram=create_telegram,
        make_login=create_login,
    )
    app.state.runtime = runtime
    await prepare(runtime)
    auth = AuthService(runtime)
    sweepers = [
        asyncio.create_task(TaskSweeper(runtime.store).run()),
        asyncio.create_task(auth.run_sweeper()),
        asyncio.create_task(auth.run_keepalive()),
    ]
    try:
        yield
    finally:
        for sweeper in sweepers:
            sweeper.cancel()
        await auth.close()
        await runtime.stop()
        await runtime.telegram.stop()
        await runtime.download.stop()
        await redis.aclose()
        await engine.dispose()
        logger.info("Stopped")


app = FastAPI(**settings.app.set_app_attributes, lifespan=lifespan)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(OriginCheckMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.app.ALLOWED_ORIGINS,
    allow_credentials=settings.app.IS_ALLOWED_CREDENTIALS,
    allow_methods=settings.app.ALLOWED_METHODS,
    allow_headers=settings.app.ALLOWED_HEADERS,
    expose_headers=settings.app.EXPOSED_HEADERS,
)
app.include_router(routers)
register_exception_handlers(app)


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.app.HOST, port=settings.app.PORT)
