from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from tests.api.conftest import path, upload
from tests.factories import PDF

TEXT = b"activity"


def code(response) -> str:
    return response.json()["error"]["code"]


async def put(client, storage, rest: str, data: bytes = TEXT) -> dict:
    task = await upload(client, path(storage, rest), data)
    assert task["status"] == "done", task
    return task["file"]


async def backdate(engine, file: dict, days: int) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE files SET created_at = :at WHERE id = :id"),
            {"at": datetime.now(UTC) - timedelta(days=days), "id": file["uuid"]},
        )


async def activity(client, **params) -> dict:
    response = await client.get("/files/activity", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def day(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).date().isoformat()


async def test_activity_groups_uploads_by_day(client, storage, engine):
    await put(client, storage, "a.txt")
    await put(client, storage, "b/c.pdf", PDF)
    old = await put(client, storage, "old.txt")
    older = await put(client, storage, "older.txt")
    ancient = await put(client, storage, "ancient.txt")
    await backdate(engine, old, 3)
    await backdate(engine, older, 3)
    await backdate(engine, ancient, 400)
    result = await activity(client)
    assert result["days"] == 365
    assert result["until"] == day(0) and result["since"] == day(364)
    assert result["items"] == [
        {"date": day(3), "files": 2, "size": 2 * len(TEXT)},
        {"date": day(0), "files": 2, "size": len(TEXT) + len(PDF)},
    ]
    assert (result["files"], result["size"]) == (4, 3 * len(TEXT) + len(PDF))
    short = await activity(client, days=3)
    assert short["since"] == day(2) and [item["date"] for item in short["items"]] == [
        day(0)
    ]
    assert (await activity(client, days=366))["files"] == 4


async def test_activity_scopes_and_access(client, storage, make_user):
    await put(client, storage, "team/inbox/a.txt")
    await put(client, storage, "team/inbox/sub/b.txt")
    await put(client, storage, "team/secret/c.txt")
    other = (await client.post("/storages", json={"name": "Другое"})).json()
    await put(client, other, "d.txt")
    assert (await activity(client))["files"] == 4
    assert (await activity(client, path=path(storage)))["files"] == 3
    assert (await activity(client, path=path(storage, "team/inbox/sub")))["files"] == 1
    user = await make_user("active", "user", {"path": path(storage, "team/inbox")})
    assert (await activity(user))["files"] == 2
    assert (await activity(user, path=path(storage, "team")))["files"] == 2
    response = await user.get(
        "/files/activity", params={"path": path(storage, "team/secret")}
    )
    assert response.status_code == 403 and code(response) == "forbidden"
    nobody = await make_user("idle")
    assert (await activity(nobody))["items"] == []


async def test_activity_validates_input(client, storage):
    for days in (0, 367, "x"):
        response = await client.get("/files/activity", params={"days": days})
        assert response.status_code == 422 and code(response) == "validation_error"
    missing = await client.get(
        "/files/activity", params={"path": path(storage, "nope")}
    )
    assert missing.status_code == 404 and code(missing) == "folder_not_found"
