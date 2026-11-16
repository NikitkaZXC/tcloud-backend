from tests.api.conftest import path, upload
from tests.factories import PDF, make_image


async def stored(client, storage, name: str, data: bytes = PDF, **options) -> dict:
    task = await upload(client, path(storage, name), data, **options)
    assert task["status"] == "done", task
    return task["file"]


async def test_deleted_in_telegram_removes_record(client, telegram, storage):
    gone = await stored(client, storage, "a/gone.pdf")
    kept = await stored(client, storage, "a/kept.pdf")
    chat_id = gone["telegram"]["chat_id"]
    await telegram.delete_by_hand(chat_id, [gone["telegram"]["message_id"]])
    assert (await client.get(f"/files/{gone['uuid']}")).status_code == 404
    assert (await client.get(f"/files/{kept['uuid']}")).status_code == 200
    listing = (await client.get("/folders", params={"path": path(storage, "a")})).json()
    assert [file["name"] for file in listing["files"]] == ["kept.pdf"]


async def test_many_deleted_at_once(client, telegram, storage):
    files = [await stored(client, storage, f"{index}.pdf") for index in range(5)]
    chat_id = files[0]["telegram"]["chat_id"]
    await telegram.delete_by_hand(
        chat_id, [file["telegram"]["message_id"] for file in files[:4]]
    )
    remaining = [
        (await client.get(f"/files/{file['uuid']}")).status_code for file in files
    ]
    assert remaining == [404, 404, 404, 404, 200]


async def test_deletion_in_other_chat_ignored(client, telegram, storage):
    file = await stored(client, storage, "a.pdf")
    other = telegram.add_channel("not a storage")
    await telegram.delete_by_hand(other, [file["telegram"]["message_id"]])
    assert (await client.get(f"/files/{file['uuid']}")).status_code == 200


async def test_same_message_id_in_other_storage_ignored(client, telegram, storage):
    file = await stored(client, storage, "a.pdf")
    assert (await client.post("/storages", json={"name": "Второе"})).status_code == 201
    other = next(
        chat_id for chat_id, chat in telegram.chats.items() if chat["title"] == "Второе"
    )
    await telegram.on_deleted(other, [file["telegram"]["message_id"]])
    assert (await client.get(f"/files/{file['uuid']}")).status_code == 200


async def test_old_message_deleted_after_replace_keeps_file(client, telegram, storage):
    first = await stored(client, storage, "a.jpg", make_image())
    second = await stored(
        client, storage, "a.jpg", make_image("PNG"), on_conflict="replace"
    )
    assert first["uuid"] == second["uuid"]
    old = first["telegram"]["message_id"]
    assert old != second["telegram"]["message_id"]
    await telegram.on_deleted(first["telegram"]["chat_id"], [old])
    assert (await client.get(f"/files/{first['uuid']}")).status_code == 200


async def test_deleted_by_api_then_event_is_harmless(client, telegram, storage):
    file = await stored(client, storage, "a.pdf")
    assert (await client.delete(f"/files/{file['uuid']}")).status_code == 204
    await telegram.on_deleted(
        file["telegram"]["chat_id"], [file["telegram"]["message_id"]]
    )
    assert (await client.get(f"/files/{file['uuid']}")).status_code == 404


async def test_download_of_gone_message_removes_record(client, telegram, storage):
    file = await stored(client, storage, "a.pdf")
    chat_id = file["telegram"]["chat_id"]
    telegram.chats[chat_id]["messages"].pop(file["telegram"]["message_id"])
    response = await client.get(f"/files/{file['uuid']}/download")
    assert (
        response.status_code == 404
        and response.json()["error"]["code"] == "file_not_found"
    )
    assert (await client.get(f"/files/{file['uuid']}")).status_code == 404
