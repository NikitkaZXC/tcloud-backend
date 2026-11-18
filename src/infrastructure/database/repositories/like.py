from uuid import UUID

from sqlalchemy import ColumnElement, and_, false, or_, true
from sqlalchemy.orm import InstrumentedAttribute


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def folder_and_below(
    column: InstrumentedAttribute[str], folder: str
) -> ColumnElement[bool]:
    return or_(
        column == folder, column.like(escape_like(folder + "/") + "%", escape="\\")
    )


def readable(
    storage: InstrumentedAttribute[UUID],
    folder_path: InstrumentedAttribute[str],
    scopes: list[tuple[UUID, str, bool]] | None,
) -> ColumnElement[bool]:
    if scopes is None:
        return true()
    return or_(
        false(),
        *(
            and_(
                storage == storage_id,
                (
                    (true() if folder == "" else folder_and_below(folder_path, folder))
                    if recursive
                    else folder_path == folder
                ),
            )
            for storage_id, folder, recursive in scopes
        ),
    )
