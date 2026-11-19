import pytest
from pydantic import ValidationError

from src.application.schemas import (
    SettingsServiceRead,
    UploadServiceCreate,
    UploadSettingsServiceRead,
)
from src.infrastructure.integrations.web import UnsafeUrlError, check_webhook


def resolve(defaults: dict | None = None, **params) -> UploadServiceCreate:
    return UploadServiceCreate(path="tcloud/x/a", **params).with_defaults(
        UploadSettingsServiceRead(**(defaults or {}))
    )


def test_defaults_fill_missing_params():
    data = resolve()
    assert (data.send_as, data.on_conflict, data.priority) == (
        "file",
        "error",
        "normal",
    )
    assert data.allow_unscannable is False and data.encrypt is True
    assert data.callback_url is None


def test_settings_fill_missing_params():
    data = resolve(
        {
            "priority": "high",
            "on_conflict": "skip",
            "allow_unscannable": True,
            "webhook_url": "https://hook.example/x",
            "encrypt": False,
        }
    )
    assert (data.priority, data.on_conflict) == ("high", "skip")
    assert data.allow_unscannable is True and data.encrypt is False
    assert data.callback_url == "https://hook.example/x"


def test_explicit_params_win():
    data = resolve(
        {"priority": "high", "allow_unscannable": True, "webhook_url": "https://h/x"},
        priority="low",
        allow_unscannable=False,
        callback_url="",
        encrypt=True,
    )
    assert data.priority == "low" and data.allow_unscannable is False
    assert data.callback_url == "" and data.encrypt is True


@pytest.mark.parametrize(
    ("defaults", "params", "encrypt"),
    [
        ({"send_as": "media", "encrypt": True}, {}, False),
        ({"send_as": "media"}, {"send_as": "file"}, True),
        ({}, {"send_as": "media"}, False),
        ({"encrypt": False}, {}, False),
        ({"send_as": "media"}, {"encrypt": True}, True),
        ({}, {"send_as": "media", "encrypt": False}, False),
    ],
)
def test_encrypt_tri_state(defaults, params, encrypt):
    assert resolve(defaults, **params).encrypt is encrypt


def test_merged_keeps_only_set_keys():
    stored = SettingsServiceRead.model_validate({"uploads": {"priority": "high"}})
    patch = SettingsServiceRead.model_validate(
        {"uploads": {"send_as": "media"}, "interface": {"theme": "light"}}
    )
    merged = stored.merged(patch)
    assert merged.model_dump(mode="json", exclude_unset=True) == {
        "uploads": {"priority": "high", "send_as": "media"},
        "interface": {"theme": "light"},
    }
    assert merged.archives.format == "zip"


def test_unknown_keys_rejected():
    with pytest.raises(ValidationError):
        SettingsServiceRead.model_validate({"uploads": {"nope": 1}})
    with pytest.raises(ValidationError):
        SettingsServiceRead.model_validate({"other": {}})


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://10.1.2.3/x",
        "http://192.168.0.1/x",
        "http://169.254.169.254/x",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://0.0.0.0/x",
        "ftp://example.com/x",
        "file:///etc/passwd",
    ],
)
def test_check_webhook_rejects(url):
    with pytest.raises(UnsafeUrlError):
        check_webhook(url)


@pytest.mark.parametrize(
    "url", ["https://hooks.example.com/x?t=1", "http://8.8.8.8:8080/hook"]
)
def test_check_webhook_accepts(url):
    check_webhook(url)


def test_from_stored_drops_unknown_and_invalid():
    value, dropped = SettingsServiceRead.from_stored(
        {
            "uploads": {"priority": "high", "gone": True},
            "interface": {"theme": "neon", "folder_view": "grid"},
            "old": {"x": 1},
            "telegram": "broken",
        }
    )
    assert value.model_dump(mode="json", exclude_unset=True) == {
        "uploads": {"priority": "high"},
        "interface": {"folder_view": "grid"},
    }
    assert value.interface.theme == "dark"
    assert dropped == ["uploads.gone", "interface.theme", "old", "telegram"]


def test_from_stored_keeps_known_dashboard_blocks():
    value, dropped = SettingsServiceRead.from_stored(
        {"interface": {"dashboard_layout": ["tasks", "gone", "tasks", 7, "storages"]}}
    )
    assert value.interface.dashboard_layout == ["tasks", "storages"]
    assert dropped == ["interface.dashboard_layout"]


def test_from_stored_keeps_valid_file_columns():
    value, dropped = SettingsServiceRead.from_stored(
        {
            "interface": {
                "file_columns": {"name": 300, "owner": 100, "size": 10, "type": "x"},
                "gone": [1],
            }
        }
    )
    assert value.interface.file_columns == {"name": 300}
    assert dropped == ["interface.file_columns", "interface.gone"]


@pytest.mark.parametrize(
    ("interface", "hidden"),
    [
        ({"activity_map": False}, ["activity_map"]),
        ({"activity_map": True}, []),
        ({"activity_map": "junk"}, []),
        (
            {"activity_map": False, "dashboard_hidden": ["users"]},
            ["users", "activity_map"],
        ),
        (
            {"activity_map": False, "dashboard_hidden": ["activity_map"]},
            ["activity_map"],
        ),
    ],
)
def test_from_stored_migrates_activity_map_silently(interface, hidden):
    value, dropped = SettingsServiceRead.from_stored({"interface": interface})
    assert value.interface.dashboard_hidden == hidden
    assert "activity_map" not in value.interface.model_dump()
    assert dropped == []


def test_from_stored_keeps_known_hidden_blocks():
    value, dropped = SettingsServiceRead.from_stored(
        {"interface": {"dashboard_hidden": ["users", "gone", "users", "tasks"]}}
    )
    assert value.interface.dashboard_hidden == ["users", "tasks"]
    assert dropped == ["interface.dashboard_hidden"]
