import pytest
from pydantic import ValidationError

from src.config.models import AppSettings, RootSettings, TelegramSettings


def test_chats_parsed():
    assert TelegramSettings(
        API_ID=1, API_HASH="x", CHATS=" -1001, -1002 ,"
    ).CHAT_IDS == [-1001, -1002]


def test_chats_must_be_ids():
    with pytest.raises(ValidationError):
        TelegramSettings(API_ID=1, API_HASH="x", CHATS="Фото,-100123")


def test_no_chats():
    assert TelegramSettings(API_ID=1, API_HASH="x").CHAT_IDS == []


def test_unreadable_jwt_key_fails_fast(tmp_path, monkeypatch):
    from src.config import settings
    from src.infrastructure.security import jwt

    monkeypatch.setattr(settings.jwt, "PRIVATE_KEY_PATH", tmp_path / "missing.pem")
    jwt._private_key.cache_clear()
    with pytest.raises(RuntimeError, match="not readable"):
        jwt.check_keys()
    jwt._private_key.cache_clear()


def test_jwt_keys_are_parsed_once():
    from src.infrastructure.security import jwt

    assert jwt._public_key() is jwt._public_key()
    assert jwt._private_key() is jwt._private_key()
    assert not isinstance(jwt._public_key(), (str, bytes))


@pytest.mark.parametrize("key", ["", "x" * 31])
def test_short_secret_key_rejected(key):
    with pytest.raises(ValidationError) as error:
        AppSettings(SECRET_KEY=key)
    assert "x" * 31 not in str(error.value)


def test_secret_key_of_32_chars_accepted():
    assert AppSettings(SECRET_KEY="x" * 32).SECRET_KEY.get_secret_value() == "x" * 32


@pytest.mark.parametrize("password", ["", "x" * 11])
def test_short_root_password_rejected(password):
    with pytest.raises(ValidationError) as error:
        RootSettings(LOGIN="admin", PASSWORD=password)
    assert "x" * 11 not in str(error.value)


@pytest.mark.parametrize("login", ["", "a" * 65])
def test_bad_root_login_length_rejected(login):
    with pytest.raises(ValidationError):
        RootSettings(LOGIN=login, PASSWORD="x" * 12)


def test_root_login_of_64_chars_accepted():
    assert RootSettings(LOGIN="a" * 64, PASSWORD="x" * 12).LOGIN == "a" * 64


def test_root_password_of_12_chars_accepted():
    assert RootSettings(LOGIN="admin", PASSWORD="x" * 12).PASSWORD


async def test_empty_docs_urls_disable_swagger():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    app_settings = AppSettings(
        SECRET_KEY="x" * 32, DOCS_URL="", REDOC_URL="", OPENAPI_URL=""
    )
    assert app_settings.docs_paths == ()
    app = FastAPI(**app_settings.set_app_attributes)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for url in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            assert (await client.get(url)).status_code == 404


def test_default_docs_paths():
    assert AppSettings(SECRET_KEY="x" * 32).docs_paths == (
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
    )
