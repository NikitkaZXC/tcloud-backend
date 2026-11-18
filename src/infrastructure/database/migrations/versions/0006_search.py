"""files and folders: search indexes (pg_trgm on names, filter and sort columns)

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_files_name_trgm ON files USING gin (lower(name) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_folders_path_trgm ON folders USING gin (lower(path) gin_trgm_ops)"
    )
    op.create_index("ix_files_name", "files", ["name"])
    op.create_index("ix_files_kind", "files", ["kind"])
    op.create_index("ix_files_mime", "files", ["mime"])
    op.create_index("ix_files_size", "files", ["size"])
    op.create_index("ix_files_updated_at", "files", ["updated_at"])
    op.create_index(
        "ix_files_encrypted",
        "files",
        ["storage_id"],
        postgresql_where="encryption_header IS NOT NULL",
    )


def downgrade() -> None:
    for name in (
        "ix_files_encrypted",
        "ix_files_updated_at",
        "ix_files_size",
        "ix_files_mime",
        "ix_files_kind",
        "ix_files_name",
        "ix_folders_path_trgm",
        "ix_files_name_trgm",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")
