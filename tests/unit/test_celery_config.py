from src.infrastructure.background.celery.app import app


def test_pidbox_queue_survives_reconnect_with_stale_connection():
    queue = app.control.mailbox.get_queue("telegram@host")
    assert not queue.exclusive and queue.durable and queue.expires
