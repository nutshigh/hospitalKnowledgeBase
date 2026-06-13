import json
import pika
from typing import Callable, Optional
from dataclasses import dataclass, field

from app.config import settings


@dataclass
class TaskMessage:
    task_type: str
    hospital_id: str
    priority: int = 0
    payload: dict = field(default_factory=dict)


class RabbitMQClient:
    EXCHANGE = "hospital.tasks"
    QUEUES = {
        "parsing.urgent": "parsing.urgent",
        "parsing.normal": "parsing.normal",
        "interpretation.urgent": "interpretation.urgent",
        "interpretation.normal": "interpretation.normal",
    }
    DEAD_LETTER_QUEUE = "dead.letter"

    def __init__(self):
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[pika.channel.Channel] = None

    def _connect(self):
        credentials = pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD)
        params = pika.ConnectionParameters(
            host=settings.RABBITMQ_HOST, port=settings.RABBITMQ_PORT, credentials=credentials
        )
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()

    def _ensure_resources(self):
        self.channel.exchange_declare(exchange=self.EXCHANGE, exchange_type="topic", durable=True)
        for queue in self.QUEUES.values():
            self.channel.queue_declare(queue=queue, durable=True)
            self.channel.queue_bind(exchange=self.EXCHANGE, queue=queue, routing_key=queue)
        self.channel.queue_declare(queue=self.DEAD_LETTER_QUEUE, durable=True)

    def publish(self, task: TaskMessage):
        if not self.connection or self.connection.is_closed:
            self._connect()
            self._ensure_resources()
        routing_key = f"{task.task_type}.{'urgent' if task.priority else 'normal'}"
        try:
            self.channel.basic_publish(
                exchange=self.EXCHANGE,
                routing_key=routing_key,
                body=json.dumps({
                    "task_type": task.task_type,
                    "hospital_id": task.hospital_id,
                    "payload": task.payload,
                }),
                properties=pika.BasicProperties(delivery_mode=2),
            )
        except (pika.exceptions.ConnectionClosed, pika.exceptions.StreamLostError, pika.exceptions.ChannelClosed):
            self._connect()
            self._ensure_resources()
            self.channel.basic_publish(
                exchange=self.EXCHANGE,
                routing_key=routing_key,
                body=json.dumps({
                    "task_type": task.task_type,
                    "hospital_id": task.hospital_id,
                    "payload": task.payload,
                }),
                properties=pika.BasicProperties(delivery_mode=2),
            )

    def consume(self, queue: str, callback: Callable, prefetch_count: int = 1):
        if not self.connection or self.connection.is_closed:
            self._connect()
            self._ensure_resources()
        self.channel.basic_qos(prefetch_count=prefetch_count)

        def _callback(ch, method, properties, body):
            try:
                message = json.loads(body)
                callback(message)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        self.channel.basic_consume(queue=queue, on_message_callback=_callback)

    def start_consuming(self):
        self.channel.start_consuming()

    def close(self):
        if self.connection and self.connection.is_open:
            self.connection.close()


rabbitmq = RabbitMQClient()
