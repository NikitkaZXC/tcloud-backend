from loguru import logger

from src.infrastructure.logger import setup_logging


def test_tracebacks_do_not_show_variable_values(capsys):
    setup_logging()
    password = "hunter2-secret"
    try:
        raise ValueError(len(password))
    except ValueError:
        logger.exception("login failed")
    assert "hunter2-secret" not in capsys.readouterr().err
