from kombu import Connection, Consumer

from src.infrastructure.background import app
from src.infrastructure.enums import TaskPriority, UploadQueue


def test_high_priority_overtakes_queued_low(rabbitmq_url):
    queue = app.amqp.queues[UploadQueue.PREPARE.value]
    order = [TaskPriority.LOW] * 5 + [TaskPriority.HIGH, TaskPriority.NORMAL]
    with Connection(rabbitmq_url) as connection:
        bound = queue(connection.default_channel)
        bound.declare()
        bound.purge()
        producer = app.amqp.Producer(connection)
        for index, priority in enumerate(order):
            app.send_task(
                UploadQueue.PREPARE.value,
                kwargs={"task_id": str(index)},
                queue=UploadQueue.PREPARE.value,
                priority=priority.level,
                producer=producer,
            )
        received = []

        def collect(body, message):
            received.append(body[1]["task_id"])
            message.ack()

        with Consumer(
            connection, queues=[queue], callbacks=[collect], prefetch_count=1
        ):
            while len(received) < len(order):
                connection.drain_events(timeout=10)
    assert received[:2] == ["5", "6"] and sorted(received[2:]) == list("01234")
