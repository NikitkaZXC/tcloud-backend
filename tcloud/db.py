from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    mime         TEXT,
    size         INTEGER NOT NULL,
    sha256       TEXT,
    salt         BLOB NOT NULL,
    segment_size INTEGER NOT NULL,
    part_size    INTEGER NOT NULL,
    parts_total  INTEGER NOT NULL,
    status       TEXT NOT NULL,
    error        TEXT,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parts (
    file_id    TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    size       INTEGER NOT NULL,
    PRIMARY KEY (file_id, idx)
);
"""


@dataclass
class Part:
    idx: int
    chat_id: int
    message_id: int
    size: int


@dataclass
class FileRecord:
    id: str
    name: str
    mime: str | None
    size: int
    sha256: str | None
    salt: bytes
    segment_size: int
    part_size: int
    parts_total: int
    status: str  # uploading | ready | failed
    error: str | None
    created_at: str
    parts: list[Part] = field(default_factory=list)


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "database is not open"
        return self._conn

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def create_file(self, rec: FileRecord) -> None:
        await self.conn.execute(
            "INSERT INTO files (id, name, mime, size, sha256, salt, segment_size, part_size, parts_total, status, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rec.id, rec.name, rec.mime, rec.size, rec.sha256, rec.salt, rec.segment_size, rec.part_size,
             rec.parts_total, rec.status, rec.error, rec.created_at),
        )
        await self.conn.commit()

    async def add_part(self, file_id: str, part: Part) -> None:
        await self.conn.execute(
            "INSERT INTO parts (file_id, idx, chat_id, message_id, size) VALUES (?, ?, ?, ?, ?)",
            (file_id, part.idx, part.chat_id, part.message_id, part.size),
        )
        await self.conn.commit()

    async def set_status(self, file_id: str, status: str, *, error: str | None = None, sha256: str | None = None) -> None:
        await self.conn.execute(
            "UPDATE files SET status = ?, error = ?, sha256 = COALESCE(?, sha256) WHERE id = ?",
            (status, error, sha256, file_id),
        )
        await self.conn.commit()

    async def fail_interrupted(self) -> list[str]:
        """Загрузки, оборванные рестартом, помечаем как failed."""
        rows = await (await self.conn.execute("SELECT id FROM files WHERE status = 'uploading'")).fetchall()
        await self.conn.execute(
            "UPDATE files SET status = 'failed', error = 'interrupted by restart' WHERE status = 'uploading'"
        )
        await self.conn.commit()
        return [r["id"] for r in rows]

    async def get_file(self, file_id: str) -> FileRecord | None:
        row = await (await self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,))).fetchone()
        if row is None:
            return None
        rec = _row_to_file(row)
        parts = await (await self.conn.execute(
            "SELECT idx, chat_id, message_id, size FROM parts WHERE file_id = ? ORDER BY idx", (file_id,)
        )).fetchall()
        rec.parts = [Part(r["idx"], r["chat_id"], r["message_id"], r["size"]) for r in parts]
        return rec

    async def list_files(self, limit: int = 100, offset: int = 0) -> list[FileRecord]:
        rows = await (await self.conn.execute(
            "SELECT * FROM files ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )).fetchall()
        return [_row_to_file(r) for r in rows]

    async def delete_file(self, file_id: str) -> None:
        await self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        await self.conn.commit()


def _row_to_file(row: aiosqlite.Row) -> FileRecord:
    return FileRecord(
        id=row["id"], name=row["name"], mime=row["mime"], size=row["size"], sha256=row["sha256"],
        salt=row["salt"], segment_size=row["segment_size"], part_size=row["part_size"],
        parts_total=row["parts_total"], status=row["status"], error=row["error"], created_at=row["created_at"],
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
