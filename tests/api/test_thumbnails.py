import pytest
from httpx import ASGITransport, AsyncClient

import main
from tests.api.conftest import path, upload
from tests.factories import PDF, make_image


def code(response) -> str:
    return response.json()["error"]["code"]


def message(telegram, file: dict) -> dict:
    return telegram.chats[file["telegram"]["chat_id"]]["messages"][
        file["telegram"]["message_id"]
    ]


@pytest.fixture
async def photo(client, storage):
    return (
        await upload(
            client, path(storage, "a/photo.jpg"), make_image(), encrypt="false"
        )
    )["file"]


async def test_photo_thumbnail(client, telegram, photo):
    response = await client.get(f"/files/{photo['uuid']}/thumb")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == message(telegram, photo)["data"]
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert response.headers["etag"] == f'"{photo["telegram"]["message_id"]}"'
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_thumbnail_not_modified_skips_telegram(client, download_telegram, photo):
    tag = (await client.get(f"/files/{photo['uuid']}/thumb")).headers["etag"]
    download_telegram.authorized = False
    response = await client.get(
        f"/files/{photo['uuid']}/thumb", headers={"If-None-Match": tag}
    )
    assert response.status_code == 304 and response.content == b""
    assert response.headers["etag"] == tag
    stale = await client.get(
        f"/files/{photo['uuid']}/thumb", headers={"If-None-Match": '"0"'}
    )
    assert stale.status_code == 503 and code(stale) == "telegram_unauthorized"


async def test_photo_sent_as_file_has_thumbnail(client, storage):
    file = (
        await upload(
            client,
            path(storage, "b.png"),
            make_image("PNG"),
            send_as="file",
            encrypt="false",
        )
    )["file"]
    response = await client.get(f"/files/{file['uuid']}/thumb")
    assert response.status_code == 200 and response.content


async def test_document_without_thumbnail(client, storage):
    file = (await upload(client, path(storage, "doc.pdf"), PDF, encrypt="false"))[
        "file"
    ]
    response = await client.get(f"/files/{file['uuid']}/thumb")
    assert response.status_code == 404 and code(response) == "thumbnail_not_found"


async def test_document_with_telegram_thumbnail(client, telegram, storage):
    file = (await upload(client, path(storage, "doc.pdf"), PDF, encrypt="false"))[
        "file"
    ]
    message(telegram, file)["thumb"] = b"\xff\xd8jpeg"
    response = await client.get(f"/files/{file['uuid']}/thumb")
    assert response.status_code == 200 and response.content == b"\xff\xd8jpeg"


async def test_encrypted_file_has_no_thumbnail(client, telegram, storage):
    file = (
        await upload(client, path(storage, "s.png"), make_image("PNG"), encrypt="true")
    )["file"]
    assert file["encrypted"] is True
    message(telegram, file)["thumb"] = b"leak"
    response = await client.get(f"/files/{file['uuid']}/thumb")
    assert response.status_code == 404 and code(response) == "thumbnail_not_found"


async def test_thumbnail_of_missing_file(client):
    response = await client.get("/files/01900000-0000-7000-8000-000000000000/thumb")
    assert response.status_code == 404 and code(response) == "file_not_found"


async def test_thumbnail_of_gone_message_removes_record(client, telegram, photo):
    message_id = photo["telegram"]["message_id"]
    telegram.chats[photo["telegram"]["chat_id"]]["messages"].pop(message_id)
    response = await client.get(f"/files/{photo['uuid']}/thumb")
    assert response.status_code == 404 and code(response) == "file_not_found"
    assert (await client.get(f"/files/{photo['uuid']}")).status_code == 404


async def test_thumbnail_needs_read_access(client, storage, make_user):
    hidden = (
        await upload(
            client, path(storage, "secret/x.jpg"), make_image(), encrypt="false"
        )
    )["file"]
    shared = (
        await upload(
            client, path(storage, "shared/y.jpg"), make_image(), encrypt="false"
        )
    )["file"]
    reader = await make_user("reader", "user", {"path": path(storage, "shared")})
    assert (await reader.get(f"/files/{shared['uuid']}/thumb")).status_code == 200
    response = await reader.get(f"/files/{hidden['uuid']}/thumb")
    assert response.status_code == 403 and code(response) == "forbidden"
    stranger = await make_user("stranger")
    response = await stranger.get(f"/files/{shared['uuid']}/thumb")
    assert response.status_code == 403


async def test_thumbnail_with_api_key(client, photo):
    me = (await client.get("/users/me")).json()
    key = (
        await client.post(f"/users/{me['id']}/api-keys", json={"name": "thumbs"})
    ).json()["key"]
    async with AsyncClient(
        transport=ASGITransport(main.app), base_url="http://test/api"
    ) as anonymous:
        assert (await anonymous.get(f"/files/{photo['uuid']}/thumb")).status_code == 401
        response = await anonymous.get(
            f"/files/{photo['uuid']}/thumb", headers={"X-API-Key": key}
        )
        assert response.status_code == 200 and response.content
