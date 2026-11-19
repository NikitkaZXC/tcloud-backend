from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.infrastructure.enums import ArchiveFormat
from src.utilities.formatters import OnConflict, Priority, SendAs

DashboardBlock = Literal[
    "summary",
    "previews",
    "channels",
    "storages",
    "tasks",
    "activity_map",
    "system",
    "users",
]

FileColumn = Literal["name", "type", "size", "modified", "uploaded"]

LEGACY_INTERFACE = {"activity_map"}


class SettingsGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")


def valid(group: type[SettingsGroup], key: str, value: object) -> bool:
    try:
        group.model_validate({key: value})
    except ValidationError:
        return False
    return True


class UploadSettingsServiceRead(SettingsGroup):
    encrypt: bool = True
    send_as: SendAs = "file"
    priority: Priority = "normal"
    on_conflict: OnConflict = "error"
    allow_unscannable: bool = False
    webhook_url: str | None = Field(default=None, max_length=2048)


class ArchiveSettingsServiceRead(SettingsGroup):
    format: ArchiveFormat = ArchiveFormat.ZIP
    priority: Priority = "normal"


class InterfaceSettingsServiceRead(SettingsGroup):
    language: Literal["ru", "en"] | None = None
    theme: Literal["dark", "light", "system", "sunset", "navy", "lavender", "clay"] = (
        "dark"
    )
    folder_view: Literal["list", "grid"] = "list"
    sort_by: Literal["name", "modified", "uploaded", "type", "size"] = "name"
    sort_order: Literal["asc", "desc"] = "asc"
    folders_first: bool = True
    animations: bool = True
    confirm_delete: bool = True
    start_page: Literal["dashboard", "storages", "tasks"] = "dashboard"
    tasks_filter: Literal["all", "active_and_failed"] = "all"
    fx_pet: bool = False
    fx_sounds: bool = False
    fx_confetti: bool = False
    fx_cursor: bool = False
    fx_easter_eggs: bool = True
    dashboard_layout: list[DashboardBlock] = Field(
        default_factory=list, max_length=len(get_args(DashboardBlock))
    )
    dashboard_hidden: list[DashboardBlock] = Field(
        default_factory=list, max_length=len(get_args(DashboardBlock))
    )

    file_columns: dict[FileColumn, Annotated[int, Field(ge=48, le=2000)]] = Field(
        default_factory=dict
    )

    @field_validator("dashboard_layout", "dashboard_hidden")
    @classmethod
    def unique_blocks(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("dashboard blocks must not repeat")
        return value


class TelegramSettingsServiceRead(SettingsGroup):
    chat_folder: bool = True


class SettingsServiceRead(SettingsGroup):
    uploads: UploadSettingsServiceRead = Field(
        default_factory=UploadSettingsServiceRead
    )
    archives: ArchiveSettingsServiceRead = Field(
        default_factory=ArchiveSettingsServiceRead
    )
    interface: InterfaceSettingsServiceRead = Field(
        default_factory=InterfaceSettingsServiceRead
    )
    telegram: TelegramSettingsServiceRead = Field(
        default_factory=TelegramSettingsServiceRead
    )

    def merged(self, patch: "SettingsServiceRead") -> "SettingsServiceRead":
        stored = self.model_dump(mode="json", exclude_unset=True)
        update = patch.model_dump(mode="json", exclude_unset=True)
        return SettingsServiceRead.model_validate(
            {
                group: {**stored.get(group, {}), **update.get(group, {})}
                for group in stored | update
            }
        )

    @classmethod
    def from_stored(cls, raw: dict) -> tuple["SettingsServiceRead", list[str]]:
        kept, dropped = {}, []
        interface = raw.get("interface")
        if isinstance(interface, dict) and LEGACY_INTERFACE & interface.keys():
            cleaned = {
                key: value
                for key, value in interface.items()
                if key not in LEGACY_INTERFACE
            }
            hidden = cleaned.get("dashboard_hidden", [])
            if (
                interface.get("activity_map") is False
                and isinstance(hidden, list)
                and "activity_map" not in hidden
            ):
                cleaned["dashboard_hidden"] = [*hidden, "activity_map"]
            raw = {**raw, "interface": cleaned}
        for group, values in raw.items():
            field = cls.model_fields.get(group)
            if field is None or not isinstance(values, dict):
                dropped.append(group)
                continue
            for key, value in values.items():
                if not valid(field.annotation, key, value):
                    dropped.append(f"{group}.{key}")
                    if key not in field.annotation.model_fields:
                        continue
                    if isinstance(value, dict):
                        value = {
                            item: width
                            for item, width in value.items()
                            if valid(field.annotation, key, {item: width})
                        }
                    elif not isinstance(value, list):
                        continue
                    else:
                        value = [
                            item
                            for index, item in enumerate(value)
                            if item not in value[:index]
                            and valid(field.annotation, key, [item])
                        ]
                kept.setdefault(group, {})[key] = value
        return cls.model_validate(kept), dropped
