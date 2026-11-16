import pytest
from pydantic import ValidationError

from src.config.models import TelegramSettings


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
