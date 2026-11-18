from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.infrastructure.enums import ArchiveFormat
from src.utilities.formatters import OnConflict, Priority, SendAs


class SettingsGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    activity_map: bool = True
    confirm_delete: bool = True
    start_page: Literal["dashboard", "storages", "tasks"] = "dashboard"
    tasks_filter: Literal["all", "active_and_failed"] = "all"
    fx_pet: bool = False
    fx_sounds: bool = False
    fx_confetti: bool = False
    fx_cursor: bool = False
    fx_easter_eggs: bool = False


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
        for group, values in raw.items():
            field = cls.model_fields.get(group)
            if field is None or not isinstance(values, dict):
                dropped.append(group)
                continue
            for key, value in values.items():
                try:
                    field.annotation.model_validate({key: value})
                except ValidationError:
                    dropped.append(f"{group}.{key}")
                else:
                    kept.setdefault(group, {})[key] = value
        return cls.model_validate(kept), dropped
