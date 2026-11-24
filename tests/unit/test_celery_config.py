from src.infrastructure.background.celery.app import app


def test_pidbox_queue_survives_reconnect_with_stale_connection():
    queue = app.control.mailbox.get_queue("telegram@host")
    assert not queue.exclusive and queue.durable and queue.expires


def test_publishes_wait_for_broker_confirms():
    assert app.conf.broker_transport_options["confirm_publish"] is True
    assert app.conf.task_acks_late and app.conf.task_reject_on_worker_lost
