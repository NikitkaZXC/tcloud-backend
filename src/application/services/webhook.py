import asyncio
import hashlib
import hmac
import json
from uuid import UUID

import httpx
from fastapi.encoders import jsonable_encoder
from loguru import logger
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.application.services.task import TaskService
from src.config import settings
from src.infrastructure.cache import TaskStore
from src.infrastructure.integrations import UnsafeUrlError
from src.infrastructure.integrations.web import open_url


async def send_hook(
    store: TaskStore, sessions: async_sessionmaker, task_id: UUID | str, url: str
) -> None:
    async with sessions() as session:
        view = await TaskService(session, store).get(UUID(str(task_id)), None)
    body = json.dumps(jsonable_encoder(view)).encode()
    signature = hmac.new(
        settings.app.SECRET_KEY.get_secret_value().encode(), body, hashlib.sha256
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Tcloud-Signature": f"sha256={signature}",
    }
    for delay in (1, 2, 4, None):
        try:
            async with open_url(url, "POST", headers=headers, content=body) as response:
                if response.status_code < 500:
                    return
        except UnsafeUrlError as error:
            logger.warning("Webhook {} rejected: {}", url, error)
            return
        except httpx.HTTPError as error:
            logger.warning("Webhook {} failed: {!r}", url, error)
        if delay:
            await asyncio.sleep(delay)
