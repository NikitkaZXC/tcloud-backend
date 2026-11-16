"""drop tasks: upload state moved to Redis, queue to RabbitMQ

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("tasks")


def downgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", UUID(as_uuid=True)),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("bytes_done", sa.BigInteger, nullable=False),
        sa.Column("bytes_total", sa.BigInteger),
        sa.Column("error", JSONB),
        sa.Column("warnings", JSONB, nullable=False),
        sa.Column("callback_url", sa.Text),
        sa.Column("params", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ux_tasks_active_path", "tasks", ["path"], unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_index("ix_tasks_status_created", "tasks", ["status", "created_at"])
    op.create_index("ix_tasks_created", "tasks", ["created_at"])
    op.create_index("ix_tasks_batch", "tasks", ["batch_id", "created_at"])
