import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from loguru import logger

from src.api.endpoints import routers
from src.api.exception import register_exception_handlers
from src.application.bootstrap.startup import prepare
from src.application.services import TaskSweeper
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.cache import TaskStore, create_redis
from src.infrastructure.database.engine import create_engine
from src.infrastructure.database.session import session_factory
from src.infrastructure.integrations import create_scanner, create_telegram
from src.infrastructure.logger import setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    logger.info("Starting {} v{}", settings.app.TITLE, settings.app.VERSION)
    engine = create_engine()
    session_factory.configure(bind=engine)
    telegram = create_telegram()
    await telegram.start()
    redis = create_redis()
    runtime = AppRuntime(
        telegram=telegram, scanner=create_scanner(), store=TaskStore(redis)
    )
    app.state.runtime = runtime
    await prepare(runtime)
    sweeper = asyncio.create_task(TaskSweeper(runtime.store).run())
    try:
        yield
    finally:
        sweeper.cancel()
        await telegram.stop()
        await redis.aclose()
        await engine.dispose()
        logger.info("Stopped")


app = FastAPI(title=settings.app.TITLE, version=settings.app.VERSION, lifespan=lifespan)
app.include_router(routers)
register_exception_handlers(app)


if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.app.HOST, port=settings.app.PORT)
