"""files: index on created_at for the upload activity map

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_files_created_at", "files", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_files_created_at", table_name="files")
