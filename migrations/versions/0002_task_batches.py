"""tasks: path with one active upload per path, batch_id, list indexes

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("batch_id", UUID(as_uuid=True)))
    op.add_column("tasks", sa.Column("path", sa.Text))
    op.execute("UPDATE tasks SET path = params->>'path'")
    op.alter_column("tasks", "path", nullable=False)
    # Старые незавершённые задачи могли конфликтовать по пути — они всё равно мертвы после рестарта
    op.execute("UPDATE tasks SET status = 'failed' WHERE status IN ('pending', 'running')")
    op.drop_index("ix_tasks_status", "tasks")
    op.create_index(
        "ux_tasks_active_path", "tasks", ["path"], unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_index("ix_tasks_status_created", "tasks", ["status", "created_at"])
    op.create_index("ix_tasks_created", "tasks", ["created_at"])
    op.create_index("ix_tasks_batch", "tasks", ["batch_id", "created_at"])


def downgrade() -> None:
    for name in ("ix_tasks_batch", "ix_tasks_created", "ix_tasks_status_created", "ux_tasks_active_path"):
        op.drop_index(name, "tasks")
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.drop_column("tasks", "path")
    op.drop_column("tasks", "batch_id")
