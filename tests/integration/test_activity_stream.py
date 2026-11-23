import asyncio
from uuid import uuid7

from starlette.responses import StreamingResponse

from src.application.services.file import tracked
from src.infrastructure.cache import TaskStore


async def body():
    for _ in range(1000):
        yield b"x" * 1024
        await asyncio.sleep(0.01)


async def test_stream_activity_clears_on_client_disconnect(redis, monkeypatch):
    monkeypatch.setattr("src.infrastructure.cache.tasks.ACTIVITY_TAIL", 0.2)
    store = TaskStore(redis)
    storage_id = uuid7()
    response = StreamingResponse(tracked(body(), store, storage_id, "read"))
    sent = asyncio.Event()
    disconnect = asyncio.Event()

    async def receive() -> dict:
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.body":
            sent.set()

    scope = {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "GET"}
    serving = asyncio.create_task(response(scope, receive, send))
    await sent.wait()
    assert (await store.activity([storage_id]))[storage_id]["read"]
    disconnect.set()
    await serving
    del response, serving
    await asyncio.sleep(0.4)
    assert not (await store.activity([storage_id]))[storage_id]["read"]


async def test_stream_activity_clears_when_body_ends(redis, monkeypatch):
    monkeypatch.setattr("src.infrastructure.cache.tasks.ACTIVITY_TAIL", 0.2)
    store = TaskStore(redis)
    storage_id = uuid7()

    async def short():
        yield b"a"

    assert [chunk async for chunk in tracked(short(), store, storage_id, "read")]
    assert (await store.activity([storage_id]))[storage_id]["read"]
    await asyncio.sleep(0.3)
    assert not (await store.activity([storage_id]))[storage_id]["read"]
