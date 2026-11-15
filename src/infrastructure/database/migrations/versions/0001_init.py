"""init: storages, folders, files, tasks

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def _storage_fk():
    return sa.Column("storage_id", UUID(as_uuid=True), sa.ForeignKey("storages.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "storages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tg_chat_id", sa.BigInteger, nullable=False, unique=True),
        *_ts(),
    )
    op.create_table(
        "folders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _storage_fk(),
        sa.Column("path", sa.Text, nullable=False),
        *_ts(),
        sa.UniqueConstraint("storage_id", "path"),
    )
    op.create_index("ix_folders_prefix", "folders", ["storage_id", "path"], postgresql_ops={"path": "text_pattern_ops"})
    op.create_table(
        "files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _storage_fk(),
        sa.Column("folder_path", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("mime", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("sent_as", sa.Text, nullable=False),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("duration", sa.Float),
        sa.Column("sha256", sa.Text, nullable=False),
        sa.Column("tg_message_id", sa.BigInteger, nullable=False),
        *_ts(),
        sa.UniqueConstraint("storage_id", "folder_path", "name"),
        sa.UniqueConstraint("storage_id", "tg_message_id"),
    )
    op.create_index(
        "ix_files_folder", "files", ["storage_id", "folder_path"], postgresql_ops={"folder_path": "text_pattern_ops"}
    )
    op.create_index("ix_files_sha256", "files", ["sha256"])
    op.create_table(
        "tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("bytes_done", sa.BigInteger, nullable=False),
        sa.Column("bytes_total", sa.BigInteger),
        sa.Column("error", JSONB),
        sa.Column("warnings", JSONB, nullable=False),
        sa.Column("callback_url", sa.Text),
        sa.Column("params", JSONB, nullable=False),
        *_ts(),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])


def downgrade() -> None:
    for t in ("tasks", "files", "folders", "storages"):
        op.drop_table(t)
