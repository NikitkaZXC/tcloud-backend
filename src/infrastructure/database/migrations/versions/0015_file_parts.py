"""file_parts: Telegram messages of files split into several parts

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_parts",
        sa.Column(
            "file_id",
            sa.Uuid,
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("index", sa.Integer, primary_key=True),
        sa.Column("tg_message_id", sa.BigInteger, nullable=False),
        sa.Column("offset", sa.BigInteger, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
    )
    op.create_index("ix_file_parts_message", "file_parts", ["tg_message_id"])


def downgrade() -> None:
    op.drop_table("file_parts")
