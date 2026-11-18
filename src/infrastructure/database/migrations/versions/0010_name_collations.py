"""files: ICU collations for name sort by UI language

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

COLLATIONS = {
    "tcloud_ru": "ru-u-kn-kr-cyrl-latn",
    "tcloud_en": "en-u-kn-kr-latn-cyrl",
}


def upgrade() -> None:
    for name, locale in COLLATIONS.items():
        op.execute(
            f"CREATE COLLATION IF NOT EXISTS {name} (provider = icu, locale = '{locale}')"
        )
        op.execute(f"CREATE INDEX ix_files_name_{name[7:]} ON files (name COLLATE {name})")


def downgrade() -> None:
    for name in COLLATIONS:
        op.execute(f"DROP INDEX IF EXISTS ix_files_name_{name[7:]}")
        op.execute(f"DROP COLLATION IF EXISTS {name}")
