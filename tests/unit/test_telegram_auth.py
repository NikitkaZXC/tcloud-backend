import stat
from pathlib import Path

import pytest
from telethon import errors

from src.infrastructure.integrations import (
    TelegramCodeInvalidError,
    TelegramError,
    TelegramPasswordInvalidError,
    TelegramPasswordNeededError,
    TelegramPhoneInvalidError,
    TelegramSessionTooFreshError,
    TelegramUnauthorizedError,
)
from src.infrastructure.integrations.telegram.client import translate_errors
from src.infrastructure.integrations.telegram.login import TelegramLogin, session_file


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (errors.SessionPasswordNeededError, TelegramPasswordNeededError),
        (errors.PhoneCodeInvalidError, TelegramCodeInvalidError),
        (errors.PhoneCodeExpiredError, TelegramCodeInvalidError),
        (errors.PasswordHashInvalidError, TelegramPasswordInvalidError),
        (errors.PhoneNumberInvalidError, TelegramPhoneInvalidError),
        (errors.FreshResetAuthorisationForbiddenError, TelegramSessionTooFreshError),
        (errors.AuthKeyUnregisteredError, TelegramUnauthorizedError),
        (errors.SessionRevokedError, TelegramUnauthorizedError),
        (errors.ChatAdminRequiredError, TelegramError),
    ],
)
async def test_login_errors_are_translated(raised, expected):
    @translate_errors
    async def call():
        raise raised(request=None)

    with pytest.raises(expected):
        await call()


def test_session_file_name():
    assert session_file(Path("/s/tcloud")).name == "tcloud.session"
    assert session_file(Path("/s/a.session")).name == "a.session"


async def test_finished_login_replaces_session_privately(tmp_path):
    target = tmp_path / "tcloud.session"
    target.write_bytes(b"old")
    login = TelegramLogin(tmp_path / "tcloud", 1, "hash", "tcloud-api")
    temp = tmp_path / "login-tcloud.session"
    assert temp.exists()
    await login.finish()
    assert not temp.exists() and target.read_bytes() != b"old"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


async def test_closed_login_leaves_no_file(tmp_path):
    login = TelegramLogin(tmp_path / "tcloud", 1, "hash", "tcloud-api")
    await login.close()
    assert list(tmp_path.iterdir()) == []
