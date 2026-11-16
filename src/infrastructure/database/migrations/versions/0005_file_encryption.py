"""files: encryption header of files encrypted with the server key

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("files", sa.Column("encryption_header", sa.LargeBinary))


def downgrade() -> None:
    op.drop_column("files", "encryption_header")
