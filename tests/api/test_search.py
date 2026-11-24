from datetime import UTC, datetime, timedelta

import pytest

from tests.api.conftest import path, upload
from tests.factories import PDF, make_gif_animated, make_image

TEXT = b"plain text file"


def code(response) -> str:
    return response.json()["error"]["code"]


async def put(client, storage, rest: str, data: bytes, **options) -> dict:
    task = await upload(client, path(storage, rest), data, **options)
    assert task["status"] == "done", task
    return task["file"]


async def search(client, **params) -> dict:
    response = await client.get("/files/search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def names(result: dict) -> list[str]:
    return [item["name"] for item in result["items"]]


@pytest.fixture
async def tree(client, storage):
    files = {
        "report": await put(client, storage, "docs/Отчёт за МАЙ.pdf", PDF),
        "notes": await put(client, storage, "docs/old/отчёт.txt", TEXT),
        "plain": await put(client, storage, "docs/план.txt", TEXT, encrypt="false"),
        "jpg": await put(client, storage, "photos/Море.JPG", make_image()),
        "jpeg": await put(
            client, storage, "photos/trip/море2.jpeg", make_image(size=(80, 60))
        ),
        "png": await put(client, storage, "photos/icon.png", make_image("PNG")),
        "gif": await put(client, storage, "fun.gif", make_gif_animated()),
        "wild": await put(client, storage, "100%_done.txt", TEXT),
    }
    return files


async def test_empty_search_is_rejected(client, storage):
    response = await client.get("/files/search")
    assert response.status_code == 422 and code(response) == "empty_search"
    response = await client.get("/files/search", params={"path": path(storage)})
    assert response.status_code == 422 and code(response) == "empty_search"
    response = await client.get("/files/search", params={"q": "   "})
    assert response.status_code == 422 and code(response) == "empty_search"


@pytest.mark.parametrize(
    "params",
    [
        {"q": "x" * 256},
        {"q": "a", "limit": 201},
        {"q": "a", "limit": 0},
        {"q": "a", "offset": -1},
        {"q": "a", "size_min": -1},
        {"q": "a", "size_min": 10, "size_max": 5},
        {"q": "a", "size_min": 10**30},
        {"q": "a", "size_max": 2**63},
        {
            "q": "a",
            "date_from": "2026-02-01T00:00:00Z",
            "date_to": "2026-01-01T00:00:00Z",
        },
        {"q": "a", "kind": "executable"},
        {"q": "a", "mime": "not a mime"},
        {"q": "a", "sort": "random"},
        {"q": "a", "order": "up"},
        {"q": "a", "lang": "de"},
        {"q": "a", "path": "x" * 1025},
    ],
)
async def test_search_validates_input(client, params):
    response = await client.get("/files/search", params=params)
    assert response.status_code == 422 and code(response) == "validation_error"


async def test_search_is_case_insensitive_for_cyrillic(client, tree):
    result = await search(client, q="ОТЧЁТ")
    assert sorted(names(result)) == ["Отчёт за МАЙ.pdf", "отчёт.txt"]
    assert result["total"] == 2 and result["has_more"] is False
    assert sorted(names(await search(client, q="море"))) == ["Море.JPG", "море2.jpeg"]


async def test_name_sort_follows_language_while_paging(client, storage):
    for name in ("b10.txt", "b2.txt", "Яблоко.txt", "apple.txt", "ёж.txt"):
        await put(client, storage, name, TEXT)

    async def pages(lang: str) -> list[str]:
        found = []
        for offset in range(0, 5, 2):
            page = await search(
                client, q=".txt", sort="name", lang=lang, limit=2, offset=offset
            )
            found += names(page)
        return found

    assert await pages("ru") == [
        "ёж.txt",
        "Яблоко.txt",
        "apple.txt",
        "b2.txt",
        "b10.txt",
    ]
    assert await pages("en") == [
        "apple.txt",
        "b2.txt",
        "b10.txt",
        "ёж.txt",
        "Яблоко.txt",
    ]
    default = await search(client, q=".txt", sort="name", order="desc")
    assert names(default)[0] == "Яблоко.txt"


async def test_relevance_puts_exact_then_prefix_first(client, tree):
    assert names(await search(client, q="отчёт.txt")) == ["отчёт.txt"]
    assert names(await search(client, q="отчёт")) == ["Отчёт за МАЙ.pdf", "отчёт.txt"]
    assert names(await search(client, q="за май")) == ["Отчёт за МАЙ.pdf"]


async def test_like_wildcards_are_literal(client, tree):
    assert names(await search(client, q="%")) == ["100%_done.txt"]
    assert names(await search(client, q="_")) == ["100%_done.txt"]
    assert names(await search(client, q="\\")) == []


async def test_items_show_where_the_file_is(client, storage, tree):
    item = (await search(client, q="море2"))["items"][0]
    assert item["uuid"] == tree["jpeg"]["uuid"]
    assert item["path"] == path(storage, "photos/trip/море2.jpeg")
    assert item["folder"] == path(storage, "photos/trip")
    assert item["storage_id"] == storage["id"] and item["storage_name"] == "Фото"
    assert item["telegram"] == tree["jpeg"]["telegram"]


async def test_filters_by_kind_and_mime(client, tree):
    photos = await search(client, kind="photo", sort="name")
    assert set(names(photos)) == {"icon.png", "Море.JPG", "море2.jpeg"}
    both = await search(client, kind=["photo", "animation"])
    assert set(names(both)) == {"icon.png", "Море.JPG", "море2.jpeg", "fun.gif"}
    jpeg = await search(client, mime="image/jpeg")
    assert set(names(jpeg)) == {"Море.JPG", "море2.jpeg"}
    upper = await search(client, mime=["IMAGE/PNG", "application/pdf"])
    assert set(names(upper)) == {"icon.png", "Отчёт за МАЙ.pdf"}
    assert names(await search(client, q="море", mime="image/png")) == []


async def test_filters_by_size_date_and_encryption(client, tree):
    exact = await search(client, size_min=len(PDF), size_max=len(PDF))
    assert names(exact) == ["Отчёт за МАЙ.pdf"]
    small = await search(client, size_max=len(TEXT), q=".txt")
    assert set(names(small)) == {"отчёт.txt", "план.txt", "100%_done.txt"}
    now = datetime.now(UTC)
    recent = await search(client, date_from=(now - timedelta(hours=1)).isoformat())
    assert recent["total"] == 8
    future = await search(client, date_from=(now + timedelta(hours=1)).isoformat())
    assert future["total"] == 0
    past = await search(client, date_to=(now - timedelta(hours=1)).isoformat())
    assert past["total"] == 0
    plain = await search(client, encrypted="false")
    assert set(names(plain)) == {"план.txt"}
    assert (await search(client, encrypted="true"))["total"] == 7


async def test_scopes_folder_storage_and_everywhere(client, storage, tree):
    other = (await client.post("/storages", json={"name": "Другое"})).json()
    await put(client, other, "отчёт другой.txt", TEXT)
    everywhere = await search(client, q="отчёт")
    assert everywhere["total"] == 3
    assert {item["storage_name"] for item in everywhere["items"]} == {"Фото", "Другое"}
    in_storage = await search(client, q="отчёт", path=path(storage))
    assert sorted(names(in_storage)) == ["Отчёт за МАЙ.pdf", "отчёт.txt"]
    subtree = await search(client, q="отчёт", path=path(storage, "docs/old"))
    assert names(subtree) == ["отчёт.txt"]
    photos = await search(client, kind="photo", path=path(storage, "photos"))
    assert photos["total"] == 3
    assert (await search(client, kind="photo", path=path(storage, "photos/trip")))[
        "total"
    ] == 1


async def test_scope_errors(client, storage):
    response = await client.get(
        "/files/search", params={"q": "a", "path": path(storage, "nope")}
    )
    assert response.status_code == 404 and code(response) == "folder_not_found"
    response = await client.get(
        "/files/search",
        params={"q": "a", "path": "tcloud/01900000-0000-7000-8000-000000000000"},
    )
    assert response.status_code == 404 and code(response) == "storage_not_found"
    response = await client.get("/files/search", params={"q": "a", "path": "bad"})
    assert response.status_code == 422 and code(response) == "invalid_path"


async def test_sorting_and_pagination(client, tree):
    by_size = await search(client, q=".", sort="size")
    sizes = [item["size"] for item in by_size["items"]]
    assert sizes == sorted(sizes, reverse=True)
    ascending = await search(client, q=".", sort="size", order="asc")
    assert [item["size"] for item in ascending["items"]] == sorted(sizes)
    by_date = await search(client, q=".", sort="date", order="asc")
    dates = [item["updated_at"] for item in by_date["items"]]
    assert dates == sorted(dates)
    by_name = await search(client, q=".", sort="name", order="desc")
    assert by_name["total"] == 8
    pages = []
    for offset in range(0, 8, 3):
        page = await search(client, q=".", sort="size", limit=3, offset=offset)
        assert page["total"] == 8 and page["limit"] == 3 and page["offset"] == offset
        assert page["has_more"] is (offset + 3 < 8)
        pages += [item["uuid"] for item in page["items"]]
    assert pages == [item["uuid"] for item in by_size["items"]]
    assert len(set(pages)) == 8


async def test_folders_match_by_their_own_name(client, storage, tree):
    result = await search(client, q="TRIP")
    assert [folder["path"] for folder in result["folders"]] == [
        path(storage, "photos/trip")
    ]
    folder = result["folders"][0]
    assert folder["name"] == "trip" and folder["storage_name"] == "Фото"
    assert [f["path"] for f in (await search(client, q="photo"))["folders"]] == [
        path(storage, "photos")
    ]
    assert (await search(client, q="trip", kind="photo"))["folders"] == []
    assert (await search(client, q="trip", offset=1))["folders"] == []
    inside = await search(client, q="o", path=path(storage, "photos"))
    assert [f["path"] for f in inside["folders"]] == []
    everywhere = await search(client, q="o")
    assert {f["name"] for f in everywhere["folders"]} == {"old", "docs", "photos"}


async def test_search_shows_only_readable_files(client, storage, make_user):
    await put(client, storage, "team/inbox/отчёт 1.txt", TEXT)
    await put(client, storage, "team/inbox/sub/отчёт 2.txt", TEXT)
    await put(client, storage, "team/flat/отчёт 3.txt", TEXT)
    await put(client, storage, "team/flat/inner/отчёт 4.txt", TEXT)
    await put(client, storage, "team/secret/отчёт 5.txt", TEXT)
    other = (await client.post("/storages", json={"name": "Чужое"})).json()
    await put(client, other, "отчёт 6.txt", TEXT)
    user = await make_user(
        "searcher",
        "user",
        {"path": path(storage, "team/inbox")},
        {"path": path(storage, "team/flat"), "recursive": False},
    )
    result = await search(user, q="отчёт")
    assert sorted(names(result)) == ["отчёт 1.txt", "отчёт 2.txt", "отчёт 3.txt"]
    assert result["total"] == 3
    assert (await search(user, kind="document"))["total"] == 3
    assert (await search(user, q="отчёт", path=path(storage)))["total"] == 3
    assert (await search(user, q="отчёт", path=path(storage, "team")))["total"] == 3
    folders = await search(user, q="inner")
    assert folders["folders"] == [] and folders["total"] == 0
    assert [f["name"] for f in (await search(user, q="sub"))["folders"]] == ["sub"]
    for target in (path(storage, "team/secret"), path(other)):
        response = await user.get("/files/search", params={"q": "a", "path": target})
        assert response.status_code == 403 and code(response) == "forbidden"
    mimes = await user.get("/files/search/mimes")
    assert [item["files"] for item in mimes.json()] == [3]
    nobody = await make_user("nobody")
    assert (await search(nobody, q="отчёт"))["total"] == 0


async def test_mime_facets(client, storage, tree):
    response = await client.get("/files/search/mimes")
    assert response.status_code == 200
    facets = {item["mime"]: item["files"] for item in response.json()}
    expected = {}
    for file in tree.values():
        expected[file["mime"]] = expected.get(file["mime"], 0) + 1
    assert facets == expected and facets["image/jpeg"] == 2
    counts = [item["files"] for item in response.json()]
    assert counts == sorted(counts, reverse=True)
    scoped = await client.get(
        "/files/search/mimes", params={"path": path(storage, "photos")}
    )
    assert {item["mime"]: item["files"] for item in scoped.json()} == {
        "image/jpeg": 2,
        "image/png": 1,
    }
    missing = await client.get(
        "/files/search/mimes", params={"path": path(storage, "nope")}
    )
    assert missing.status_code == 404


async def test_search_with_api_key(client, tree):
    me = (await client.get("/users/me")).json()
    key = (await client.post(f"/users/{me['id']}/api-keys", json={"name": "s"})).json()
    client.cookies.clear()
    response = await client.get(
        "/files/search", params={"q": "море"}, headers={"X-API-Key": key["key"]}
    )
    assert response.status_code == 200 and response.json()["total"] == 2
