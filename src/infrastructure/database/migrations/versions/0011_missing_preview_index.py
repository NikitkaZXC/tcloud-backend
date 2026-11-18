"""files: partial index for the preview backfill

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_files_missing_preview",
        "files",
        ["kind", "id"],
        postgresql_where="encryption_header IS NOT NULL AND thumbnail IS NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_files_missing_preview", table_name="files")
