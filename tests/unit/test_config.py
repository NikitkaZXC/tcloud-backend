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
