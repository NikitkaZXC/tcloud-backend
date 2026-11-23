import inspect
import itertools
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid7

import pytest
from sqlalchemy import Engine, event, text

from tests.api.conftest import listing, path

BASE = datetime(2026, 1, 1, tzinfo=UTC)
FILES = [
    ("b10.txt", 5, "document", 3, 6),
    ("b2.txt", 50, "document", 1, 2),
    ("Яблоко.pdf", 10, "document", 5, 1),
    ("apple.jpg", 30, "photo", 2, 5),
    ("Ёж.png", 20, "photo", 4, 3),
    ("ezh", 40, "document", 6, 4),
]
messages = itertools.count(1)


async def add_file(
    engine,
    storage,
    folder: str,
    name: str,
    size=1,
    kind="document",
    created=0,
    updated=0,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO files (id, storage_id, folder_path, name, size, mime, kind,"
                " sent_as, sha256, tg_message_id, created_at, updated_at) VALUES"
                " (:id, :storage, :folder, :name, :size, 'application/octet-stream',"
                " :kind, 'file', 'x', :message, :created, :updated)"
            ),
            {
                "id": uuid7(),
                "storage": storage["id"],
                "folder": folder,
                "name": name,
                "size": size,
                "kind": kind,
                "message": next(messages),
                "created": BASE + timedelta(hours=created),
                "updated": BASE + timedelta(hours=updated),
            },
        )


@pytest.fixture
async def folder(client, storage, engine) -> str:
    for name in ("d/sub", "d/Альфа"):
        assert (
            await client.post("/folders", json={"path": path(storage, name)})
        ).status_code == 201
    for name, size, kind, created, updated in FILES:
        await add_file(engine, storage, "d", name, size, kind, created, updated)
    await add_file(engine, storage, "d/sub", "inner.bin", 100)
    return path(storage, "d")


def names(data: dict) -> list[str]:
    return [item["name"] for item in data["items"]]


async def walk(client, target: str, limit: int, **params) -> list[str]:
    seen, cursor = [], None
    while True:
        page = (
            await listing(client, target, limit=limit, cursor=cursor, **params)
            if cursor
            else await listing(client, target, limit=limit, **params)
        )
        assert len(page["items"]) <= limit
        seen += names(page)
        cursor = page["next_cursor"]
        if not cursor:
            return seen


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            {"lang": "en"},
            [
                "sub",
                "Альфа",
                "apple.jpg",
                "b2.txt",
                "b10.txt",
                "ezh",
                "Ёж.png",
                "Яблоко.pdf",
            ],
        ),
        (
            {"lang": "ru"},
            [
                "Альфа",
                "sub",
                "Ёж.png",
                "Яблоко.pdf",
                "apple.jpg",
                "b2.txt",
                "b10.txt",
                "ezh",
            ],
        ),
        (
            {"lang": "en", "sort_order": "desc"},
            [
                "Альфа",
                "sub",
                "Яблоко.pdf",
                "Ёж.png",
                "ezh",
                "b10.txt",
                "b2.txt",
                "apple.jpg",
            ],
        ),
        (
            {"lang": "en", "folders_first": "false"},
            [
                "apple.jpg",
                "b2.txt",
                "b10.txt",
                "ezh",
                "sub",
                "Альфа",
                "Ёж.png",
                "Яблоко.pdf",
            ],
        ),
        (
            {"lang": "en", "sort_by": "type"},
            [
                "sub",
                "Альфа",
                "ezh",
                "apple.jpg",
                "Яблоко.pdf",
                "Ёж.png",
                "b2.txt",
                "b10.txt",
            ],
        ),
        (
            {"lang": "en", "sort_by": "type", "folders_first": "false"},
            [
                "ezh",
                "sub",
                "Альфа",
                "apple.jpg",
                "Яблоко.pdf",
                "Ёж.png",
                "b2.txt",
                "b10.txt",
            ],
        ),
        (
            {"lang": "ru", "sort_by": "type", "folders_first": "false"},
            [
                "Альфа",
                "sub",
                "ezh",
                "apple.jpg",
                "Яблоко.pdf",
                "Ёж.png",
                "b2.txt",
                "b10.txt",
            ],
        ),
        (
            {"sort_by": "size", "sort_order": "desc", "folders_first": "false"},
            [
                "sub",
                "b2.txt",
                "ezh",
                "apple.jpg",
                "Ёж.png",
                "Яблоко.pdf",
                "b10.txt",
                "Альфа",
            ],
        ),
        (
            {"sort_by": "uploaded"},
            [
                "sub",
                "Альфа",
                "b2.txt",
                "apple.jpg",
                "b10.txt",
                "Ёж.png",
                "Яблоко.pdf",
                "ezh",
            ],
        ),
        (
            {"sort_by": "modified", "sort_order": "desc"},
            [
                "Альфа",
                "sub",
                "b10.txt",
                "apple.jpg",
                "ezh",
                "Ёж.png",
                "b2.txt",
                "Яблоко.pdf",
            ],
        ),
    ],
)
async def test_sort_options_and_cursor_continuity(client, folder, params, expected):
    full = await listing(client, folder, **params)
    assert names(full) == expected
    assert full["next_cursor"] is None
    assert (full["total_folders"], full["direct_files"], full["total_files"]) == (
        2,
        6,
        7,
    )
    for limit in (1, 2, 3, 7):
        assert await walk(client, folder, limit, **params) == expected


async def test_items_carry_type_and_sizes(client, folder):
    page = await listing(client, folder, lang="en", limit=3)
    assert [item["type"] for item in page["items"]] == ["folder", "folder", "file"]
    assert (page["items"][0]["files"], page["items"][0]["size"]) == (1, 100)
    assert page["items"][2]["uuid"] and page["items"][2]["telegram"]["message_id"]
    assert page["next_cursor"]


async def test_inserts_between_pages(client, storage, engine, folder):
    first = await listing(client, folder, lang="en", folders_first="false", limit=3)
    assert names(first) == ["apple.jpg", "b2.txt", "b10.txt"]
    await add_file(engine, storage, "d", "aaa.txt")
    await add_file(engine, storage, "d", "f.txt")
    second = await listing(
        client,
        folder,
        lang="en",
        folders_first="false",
        limit=3,
        cursor=first["next_cursor"],
    )
    assert names(second) == ["ezh", "f.txt", "sub"]
    rest = await listing(
        client, folder, lang="en", folders_first="false", cursor=second["next_cursor"]
    )
    assert (
        names(rest) == ["Альфа", "Ёж.png", "Яблоко.pdf"] and rest["next_cursor"] is None
    )


async def test_cursor_is_validated(client, folder):
    page = await listing(client, folder, lang="en", limit=2)
    for params in (
        {"cursor": "garbage"},
        {"cursor": page["next_cursor"], "lang": "ru"},
        {"cursor": page["next_cursor"], "lang": "en", "sort_by": "size"},
    ):
        response = await client.get("/folders", params={"path": folder, **params})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_cursor"
    for limit in (0, 201):
        response = await client.get("/folders", params={"path": folder, "limit": limit})
        assert response.status_code == 422


async def test_defaults_come_from_settings(client, folder):
    assert (
        len((await client.get("/folders", params={"path": folder})).json()["items"])
        == 8
    )
    await client.patch(
        "/account/settings",
        json={
            "interface": {
                "sort_by": "size",
                "sort_order": "desc",
                "folders_first": False,
                "language": "ru",
            }
        },
    )
    page = await client.get("/folders", params={"path": folder, "limit": 2})
    assert names(page.json()) == ["sub", "b2.txt"]


async def test_default_limit_is_20(client, storage, engine):
    for index in range(25):
        await add_file(engine, storage, "many", f"{index}.txt")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO folders (id, storage_id, path, created_at, updated_at) VALUES (:id, :s, 'many', now(), now())"
            ),
            {"id": uuid7(), "s": storage["id"]},
        )
    page = (await client.get("/folders", params={"path": path(storage, "many")})).json()
    assert len(page["items"]) == 20 and page["next_cursor"]
    assert names(page)[:3] == ["0.txt", "1.txt", "2.txt"]


@contextmanager
def recorded(telegram, monkeypatch):
    statements: list[str] = []
    calls: list[str] = []

    def record(conn, cursor, statement, *args) -> None:
        statements.append(statement)

    for name in dir(type(telegram)):
        method = getattr(telegram, name)
        if not name.startswith("_") and inspect.iscoroutinefunction(method):

            def wrap(method=method, name=name):
                async def call(*args, **kwargs):
                    calls.append(name)
                    return await method(*args, **kwargs)

                return call

            monkeypatch.setattr(telegram, name, wrap())
    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements, calls
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def grouped(statements: list[str]) -> list[str]:
    return [item for item in statements if "GROUP BY" in item]


async def test_pages_query_only_what_they_show(client, folder, telegram, monkeypatch):
    params = {"lang": "en", "limit": 3}
    with recorded(telegram, monkeypatch) as (statements, calls):
        first = await listing(client, folder, **params)
    assert names(first) == ["sub", "Альфа", "apple.jpg"]
    assert (first["total_files"], first["total_folders"], first["direct_files"]) == (
        7,
        2,
        6,
    )
    assert len(grouped(statements)) == 1 and calls == []
    first_count = len(statements)
    with recorded(telegram, monkeypatch) as (statements, calls):
        second = await listing(client, folder, cursor=first["next_cursor"], **params)
    assert names(second) == ["b2.txt", "b10.txt", "ezh"]
    assert second["total_files"] is None and second["direct_files"] is None
    assert grouped(statements) == [] and calls == []
    assert not any("FROM folders" in item and "LIKE" in item for item in statements)
    assert len(statements) < first_count
    mixed = {"lang": "en", "limit": 4, "folders_first": "false"}
    page = await listing(client, folder, **mixed)
    with recorded(telegram, monkeypatch) as (statements, calls):
        page = await listing(client, folder, cursor=page["next_cursor"], **mixed)
    assert names(page) == ["sub", "Альфа", "Ёж.png", "Яблоко.pdf"]
    assert (page["items"][0]["files"], page["items"][0]["size"]) == (1, 100)
    [sizes] = grouped(statements)
    assert "LIKE" in sizes and calls == []
