"""files: keyset indexes for paginated folder listings

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

FILE_TYPE = r"coalesce(nullif(lower(substring(name, '\.([^.]*)$')), ''), kind)"
PLAIN = {
    "ix_files_list_size": "size",
    "ix_files_list_updated": "updated_at",
    "ix_files_list_created": "created_at",
}


def upgrade() -> None:
    for lang in ("ru", "en"):
        op.execute(
            f"CREATE INDEX ix_files_list_name_{lang} ON files "
            f"(storage_id, folder_path, (name COLLATE tcloud_{lang}), id)"
        )
        op.execute(
            f"CREATE INDEX ix_files_list_type_{lang} ON files (storage_id, folder_path, "
            f"({FILE_TYPE} COLLATE tcloud_{lang}), (name COLLATE tcloud_{lang}), id)"
        )
    for name, key in PLAIN.items():
        op.create_index(name, "files", ["storage_id", "folder_path", key, "id"])


def downgrade() -> None:
    for name in PLAIN:
        op.drop_index(name, table_name="files")
    for lang in ("ru", "en"):
        op.execute(f"DROP INDEX IF EXISTS ix_files_list_type_{lang}")
        op.execute(f"DROP INDEX IF EXISTS ix_files_list_name_{lang}")
