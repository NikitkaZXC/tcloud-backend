from sqlalchemy import ColumnElement, or_
from sqlalchemy.orm import InstrumentedAttribute


def escape_like(value: str) -> str:
    return value.replace("%", r"\%").replace("_", r"\_")


def folder_and_below(
    column: InstrumentedAttribute[str], folder: str
) -> ColumnElement[bool]:
    return or_(
        column == folder, column.like(escape_like(folder + "/") + "%", escape="\\")
    )
