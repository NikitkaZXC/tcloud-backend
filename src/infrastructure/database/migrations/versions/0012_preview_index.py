"""files: partial index of stored previews for clearing them

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_files_preview",
        "files",
        ["id"],
        postgresql_where="thumbnail IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("ix_files_preview", table_name="files")
