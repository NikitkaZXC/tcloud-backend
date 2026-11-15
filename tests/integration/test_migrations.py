from alembic import command
from alembic.config import Config


def test_models_match_migrations(migrated: None, alembic_config: Config):
    command.check(alembic_config)


def test_downgrade_and_upgrade_again(migrated: None, alembic_config: Config):
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)
