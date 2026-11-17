import asyncio

import pytest

import main
from src.infrastructure.enums import TelegramSession
from src.infrastructure.integrations import TelegramUnauthorizedError
from src.infrastructure.integrations.telegram.factory import session_path
from src.infrastructure.integrations.telegram.login import session_file
from tests.api.conftest import path, start_upload, upload, wait_task
from tests.factories import PDF
from tests.fakes import FakeTelegram

API_SESSION = session_file(session_path(TelegramSession.API))


@pytest.fixture
def telegram():
    API_SESSION.unlink(missing_ok=True)
    yield FakeTelegram(session_path(TelegramSession.API))
    API_SESSION.unlink(missing_ok=True)


@pytest.fixture
def logged_in(telegram):
    API_SESSION.parent.mkdir(parents=True, exist_ok=True)
    API_SESSION.write_bytes(b"session")
    return telegram


def sessions(status: dict) -> dict:
    return {item["session"]: item for item in status["sessions"]}


async def state(client, session: str) -> dict:
    return sessions((await client.get("/telegram")).json())[session]


async def until(check, timeout: float = 5) -> None:
    for _ in range(int(timeout / 0.01)):
        if await check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


async def test_starts_without_telegram_session(client):
    api = await state(client, "api")
    assert api == {"session": "api", "authorized": False, "login": None}
    response = await client.post("/storages", json={"name": "Фото"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "telegram_unauthorized"
    health = (await client.get("/health")).json()
    assert health["telegram"] is False and health["ok"] is False


async def test_qr_login_with_two_step_password(client, telegram, logins):
    response = await client.post("/telegram/api/qr")
    assert response.status_code == 200
    qr = response.json()
    assert qr["url"].startswith("tg://login?token=") and qr["expires_at"]
    assert response.headers["cache-control"] == "no-store"
    assert (await state(client, "api"))["login"]["step"] == "qr"

    login = logins[TelegramSession.API]
    login.two_step = True
    login.scanned.set()

    async def asks_password():
        return (await state(client, "api"))["login"]["step"] == "password"

    await until(asks_password)
    wrong = await client.post("/telegram/api/password", json={"password": "nope"})
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "invalid_password"
    done = await client.post("/telegram/api/password", json={"password": login.secret})
    assert done.status_code == 200
    assert done.json() == {"session": "api", "authorized": True, "login": None}
    assert telegram.authorized and telegram.on_deleted is not None
    assert (await client.post("/storages", json={"name": "Фото"})).status_code == 201


async def test_qr_login_without_password(client, telegram, logins):
    await client.post("/telegram/api/qr")
    logins[TelegramSession.API].scanned.set()

    async def authorized():
        return (await state(client, "api"))["authorized"]

    await until(authorized)
    assert (await state(client, "api"))["login"] is None


async def test_expired_qr_reports_failure(client, logins):
    await client.post("/telegram/api/qr")
    login = logins[TelegramSession.API]
    login.expire = True
    login.scanned.set()

    async def failed():
        return (await state(client, "api"))["login"]["step"] == "failed"

    await until(failed)
    current = await state(client, "api")
    assert current["login"]["error"]["code"] == "login_expired"
    assert not current["authorized"] and login.closed
    retry = await client.post("/telegram/api/qr")
    assert retry.status_code == 200


async def test_code_login_for_worker(
    logged_in, client, upload_session, logins, storage
):
    upload_session.unlink()
    rejected = await client.post(
        "/uploads", params={"path": path(storage, "a.pdf")}, content=PDF
    )
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "telegram_unauthorized"

    sent = await client.post("/telegram/upload/code", json={"phone": "+79991234567"})
    assert sent.status_code == 200
    assert sent.json()["login"]["step"] == "code"
    assert logins[TelegramSession.UPLOAD].phone == "+79991234567"
    wrong = await client.post("/telegram/upload/sign-in", json={"code": "00000"})
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "invalid_code"
    assert (await state(client, "upload"))["login"]["step"] == "code"
    done = await client.post("/telegram/upload/sign-in", json={"code": "12345"})
    assert done.json() == {"session": "upload", "authorized": True, "login": None}
    assert upload_session.exists()
    task = await upload(client, path(storage, "a.pdf"), PDF)
    assert task["status"] == "done", task


async def test_code_login_with_two_step_password(client, logins):
    await client.post("/telegram/api/code", json={"phone": "+79991234567"})
    logins[TelegramSession.API].two_step = True
    asked = await client.post("/telegram/api/sign-in", json={"code": "12345"})
    assert asked.json()["login"]["step"] == "password"
    done = await client.post(
        "/telegram/api/password", json={"password": logins[TelegramSession.API].secret}
    )
    assert done.json()["authorized"] is True


async def test_login_steps_are_checked(logged_in, client):
    already = await client.post("/telegram/api/qr")
    assert already.status_code == 409
    assert already.json()["error"]["code"] == "already_authorized"
    missing = await client.post("/telegram/upload/sign-in", json={"code": "12345"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "login_not_found"


async def test_wrong_step_is_rejected(client):
    await client.post("/telegram/api/code", json={"phone": "+79991234567"})
    response = await client.post("/telegram/api/password", json={"password": "x"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "wrong_login_step"


async def test_login_input_is_validated(client):
    assert (
        await client.post("/telegram/api/code", json={"phone": "call me"})
    ).status_code == 422
    assert (await client.post("/telegram/main/qr")).status_code == 422
    await client.post("/telegram/api/code", json={"phone": "+79991234567"})
    assert (
        await client.post("/telegram/api/sign-in", json={"code": "abc"})
    ).status_code == 422


async def test_too_many_wrong_codes_end_the_login(client, logins):
    await client.post("/telegram/api/code", json={"phone": "+79991234567"})
    for _ in range(5):
        response = await client.post("/telegram/api/sign-in", json={"code": "00000"})
        assert response.json()["error"]["code"] == "invalid_code"
    assert (await state(client, "api"))["login"] is None
    assert logins[TelegramSession.API].closed
    response = await client.post("/telegram/api/sign-in", json={"code": "12345"})
    assert response.status_code == 404


async def test_flood_wait_on_code_is_reported(client, logins):
    response = await client.post("/telegram/api/code", json={"phone": "+70000000000"})
    assert response.status_code == 429
    assert response.json()["error"]["retry_after"] == 79200
    assert (await state(client, "api"))["login"] is None
    assert logins[TelegramSession.API].closed


async def test_login_expires(client, monkeypatch):
    monkeypatch.setattr(main.settings.telegram, "LOGIN_TTL", -1)
    await client.post("/telegram/api/code", json={"phone": "+79991234567"})
    assert (await state(client, "api"))["login"]["step"] == "expired"
    response = await client.post("/telegram/api/sign-in", json={"code": "12345"})
    assert response.status_code == 404


async def test_logout(logged_in, client, upload_session):
    response = await client.post("/telegram/upload/logout")
    assert response.json() == {"session": "upload", "authorized": False, "login": None}
    assert not upload_session.exists()
    response = await client.post("/telegram/api/logout")
    assert response.json()["authorized"] is False
    assert not API_SESSION.exists()


async def test_sessions_list_only_tcloud(logged_in, client):
    current = logged_in.add_authorization("tcloud-api", 1, current=True)
    worker = logged_in.add_authorization("tcloud-upload", 2)
    logged_in.add_authorization("iPhone 15", 100)
    listing = (await client.get("/telegram/sessions")).json()
    assert {item["id"] for item in listing} == {str(current.hash), str(worker.hash)}
    assert {item["session"] for item in listing} == {"api", "upload"}
    own = await client.delete(f"/telegram/sessions/{current.hash}")
    assert own.status_code == 409 and own.json()["error"]["code"] == "current_session"
    phone = logged_in.authorizations_list[2].hash
    assert (await client.delete(f"/telegram/sessions/{phone}")).status_code == 404
    assert (await client.delete(f"/telegram/sessions/{worker.hash}")).status_code == 204
    assert len(logged_in.authorizations_list) == 2


async def test_sweep_keeps_newest_tcloud_sessions(logged_in, client):
    from src.application.services import AuthService

    old_api = logged_in.add_authorization("tcloud-api", 300)
    current = logged_in.add_authorization("tcloud-api", 30, current=True)
    old_worker = logged_in.add_authorization("tcloud-upload", 200)
    worker = logged_in.add_authorization("tcloud-upload", 20)
    phone = logged_in.add_authorization("Telegram Desktop", 500)
    auth = AuthService(main.app.state.runtime)
    assert await auth.sweep() == 2
    assert logged_in.authorizations_list == [current, worker, phone]
    assert old_api not in logged_in.authorizations_list
    assert old_worker not in logged_in.authorizations_list


async def test_sweep_waits_while_session_is_fresh(logged_in, client):
    from src.application.services import AuthService

    logged_in.add_authorization("tcloud-upload", 200)
    logged_in.add_authorization("tcloud-upload", 1)
    logged_in.too_fresh = True
    await AuthService(main.app.state.runtime).sweep_quietly()
    assert len(logged_in.authorizations_list) == 2


async def test_revoked_worker_session_fails_task_and_reconnects(
    logged_in, client, telegram, worker, storage, monkeypatch
):
    original = telegram.send
    calls = 0

    async def send(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TelegramUnauthorizedError("Telegram session is not authorized")
        return await original(*args, **kwargs)

    monkeypatch.setattr(telegram, "send", send)
    task = await wait_task(
        client, await start_upload(client, path(storage, "a.pdf"), PDF)
    )
    assert task["status"] == "failed"
    assert task["error"]["code"] == "telegram_unauthorized"
    assert worker._telegram is None
    task = await upload(client, path(storage, "b.pdf"), PDF)
    assert task["status"] == "done", task


async def test_download_needs_its_own_session(
    logged_in, upload_session, download_session, download_telegram, make_client, logins
):
    download_session.unlink()
    client = await make_client()
    storage = (await client.post("/storages", json={"name": "Фото"})).json()
    task = await upload(client, path(storage, "a.pdf"), PDF)
    assert (await state(client, "download"))["authorized"] is False
    assert (await client.get("/health")).json()["telegram_download"] is False
    file_id = task["file"]["uuid"]
    blocked = await client.get(f"/files/{file_id}/download")
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "telegram_unauthorized"

    await client.post("/telegram/download/qr")
    logins[TelegramSession.DOWNLOAD].scanned.set()

    async def authorized():
        return (await state(client, "download"))["authorized"]

    await until(authorized)
    response = await client.get(f"/files/{file_id}/download")
    assert response.status_code == 200 and response.content == PDF
    assert download_telegram.authorized

    out = await client.post("/telegram/download/logout")
    assert out.json()["authorized"] is False and not download_session.exists()
    assert (await client.get(f"/files/{file_id}/download")).status_code == 503
