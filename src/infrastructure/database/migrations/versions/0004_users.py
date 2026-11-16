"""users: accounts with roles, refresh tokens, api keys, folder grants

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _updated():
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def _user_fk():
    return sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("login", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("blocked", sa.Boolean, nullable=False),
        sa.Column("failed_logins", sa.Integer, nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        _created(),
        _updated(),
    )
    op.create_index("ux_users_single_root", "users", ["role"], unique=True, postgresql_where=sa.text("role = 'root'"))
    op.create_table(
        "refresh_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user_fk(),
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("access_jti", UUID(as_uuid=True), nullable=False),
        sa.Column("user_agent", sa.Text),
        sa.Column("ip_address", sa.Text),
        sa.Column("signed_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False),
        _created(),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_table(
        "api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user_fk(),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("key_hash", sa.Text, nullable=False, unique=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        _created(),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_table(
        "folder_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user_fk(),
        sa.Column("storage_id", UUID(as_uuid=True), sa.ForeignKey("storages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("path", sa.Text, nullable=False),
        sa.Column("recursive", sa.Boolean, nullable=False),
        sa.Column("can_read", sa.Boolean, nullable=False),
        sa.Column("can_upload", sa.Boolean, nullable=False),
        sa.Column("can_delete", sa.Boolean, nullable=False),
        _created(),
        _updated(),
        sa.UniqueConstraint("user_id", "storage_id", "path"),
    )
    op.create_index("ix_folder_grants_storage_id", "folder_grants", ["storage_id"])


def downgrade() -> None:
    op.drop_table("folder_grants")
    op.drop_table("api_keys")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
