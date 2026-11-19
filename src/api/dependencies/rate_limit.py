from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from loguru import logger

from src.api.dependencies.auth import get_actor
from src.application.exception import RateLimitedError
from src.application.factory import get_runtime
from src.application.schemas import Actor
from src.config import settings
from src.config.models import RateLimitRule
from src.infrastructure.background import AppRuntime
from src.infrastructure.cache import RateLimitStore


def client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


class RateLimiter:
    def __init__(self, store: RateLimitStore) -> None:
        self._store = store

    async def hit(self, scope: str, identity: str, rule: RateLimitRule) -> None:
        if not settings.rate_limit.ENABLED:
            return
        try:
            count, retry_after = await self._store.hit(
                f"{scope}:{identity}", rule.limit, rule.window
            )
        except Exception:
            logger.exception(
                "Rate limit check failed, request allowed: scope={}", scope
            )
            return
        if count > rule.limit:
            logger.warning(
                "Rate limit exceeded: scope={} count={} limit={}/{}s",
                scope,
                count,
                rule.limit,
                rule.window,
            )
            raise RateLimitedError(
                "too many requests, try later", retry_after=max(retry_after, 1)
            )


def get_rate_limiter(runtime: AppRuntime = Depends(get_runtime)) -> RateLimiter:
    return RateLimiter(runtime.rate_limits)


def rate_limit_by_ip(scope: str, rule: RateLimitRule) -> Callable[..., Awaitable[None]]:
    async def dependency(
        request: Request, limiter: RateLimiter = Depends(get_rate_limiter)
    ) -> None:
        await limiter.hit(scope, f"ip:{client_ip(request)}", rule)

    return dependency


def rate_limit_by_user(
    scope: str, rule: RateLimitRule
) -> Callable[..., Awaitable[None]]:
    async def dependency(
        actor: Actor = Depends(get_actor),
        limiter: RateLimiter = Depends(get_rate_limiter),
    ) -> None:
        await limiter.hit(scope, f"user:{actor.user_id}", rule)

    return dependency


rate_limit_api = rate_limit_by_user("api", settings.rate_limit.API_USER)
