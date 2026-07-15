import json
from dataclasses import dataclass, field


@dataclass
class TaskMessage:
    task_type: str
    hospital_id: str
    priority: str = "normal"  # "urgent"|"normal"|"bulk"; 向后兼容接受 legacy int
    payload: dict = field(default_factory=dict)

    def routing_key(self) -> str:
        p = self.priority
        if isinstance(p, int):
            p = "urgent" if p else "normal"
        return f"{self.task_type}.{p}"


class _NackOnce(Exception):
    """callback 通过 raise 此异常显式控制 ack/nack 行为。"""
    def __init__(self, requeue: bool = False):
        self.requeue = requeue
        super().__init__(f"NackOnce(requeue={requeue})")


class RabbitMQClient:
    EXCHANGE = "hospital.tasks"
    DLX = "hospital.dlx"
    QUEUES = {
        "parsing.urgent": "parsing.urgent",
        "parsing.normal": "parsing.normal",
        "parsing.bulk": "parsing.bulk",
        "interpretation.urgent": "interpretation.urgent",
        "interpretation.normal": "interpretation.normal",
        "interpretation.bulk": "interpretation.bulk",
        "extract.bulk": "extract.bulk",
    }
    RETRY_QUEUES = {
        "parsing.urgent.retry": "parsing.urgent",
        "parsing.normal.retry": "parsing.normal",
        "parsing.bulk.retry": "parsing.bulk",
        "interpretation.urgent.retry": "interpretation.urgent",
        "interpretation.normal.retry": "interpretation.normal",
        "interpretation.bulk.retry": "interpretation.bulk",
        "extract.bulk.retry": "extract.bulk",
    }
    DEAD_LETTER_QUEUE = "dead.letter"

    def __init__(self):
        self.connection = None
        self.channel = None

    def _connect(self):
        import pika
        from app.config import settings
        creds = pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD)
        params = pika.ConnectionParameters(
            host=settings.RABBITMQ_HOST, port=settings.RABBITMQ_PORT,
            credentials=creds, heartbeat=0,
        )
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()

    def _ensure_resources(self):
        from app.config import settings
        ch = self.channel
        ch.exchange_declare(exchange=self.EXCHANGE, exchange_type="topic", durable=True)
        ch.exchange_declare(exchange=self.DLX, exchange_type="topic", durable=True)
        main_args = {"x-dead-letter-exchange": self.DLX, "x-dead-letter-routing-key": "dead"}
        for q in self.QUEUES.values():
            ch.queue_declare(queue=q, durable=True, arguments=main_args)
            ch.queue_bind(exchange=self.EXCHANGE, queue=q, routing_key=q)
        for rq, target in self.RETRY_QUEUES.items():
            args = {"x-dead-letter-exchange": self.EXCHANGE, "x-dead-letter-routing-key": target}
            ch.queue_declare(queue=rq, durable=True, arguments=args)
            ch.queue_bind(exchange=self.EXCHANGE, queue=rq, routing_key=rq)
        ch.queue_declare(queue=self.DEAD_LETTER_QUEUE, durable=True,
                         arguments={"x-message-ttl": settings.DEAD_LETTER_TTL * 1000})
        ch.queue_bind(exchange=self.DLX, queue=self.DEAD_LETTER_QUEUE, routing_key="dead")

    def _ensure(self):
        if not self.connection or self.connection.is_closed:
            self._connect()
            self._ensure_resources()

    def publish(self, task: TaskMessage):
        import pika
        self._ensure()
        batch_id = task.payload.get("batch_id")
        body_dict = {
            "task_type": task.task_type,
            "hospital_id": task.hospital_id,
            "payload": task.payload,
        }
        props = pika.BasicProperties(
            delivery_mode=2,
            headers={"batch_id": batch_id} if batch_id else {},
        )
        try:
            self.channel.basic_publish(
                exchange=self.EXCHANGE, routing_key=task.routing_key(),
                body=json.dumps(body_dict), properties=props,
            )
        except (pika.exceptions.ConnectionClosed, pika.exceptions.StreamLostError,
                pika.exceptions.ChannelClosed):
            self._ensure()
            self.channel.basic_publish(
                exchange=self.EXCHANGE, routing_key=task.routing_key(),
                body=json.dumps(body_dict), properties=props,
            )

    def publish_retry(self, original_routing_key: str, body: bytes, expiration_ms: int, batch_id=None):
        """把失败消息发到对应 retry 队列等待 TTL 后回流原队列。

        body 为预序列化的 raw bytes(不再 json.dumps),调用方负责序列化。
        """
        import pika
        self._ensure()
        rk_retry = f"{original_routing_key}.retry"
        if rk_retry not in self.RETRY_QUEUES:
            raise ValueError(f"no retry queue for routing_key={original_routing_key}")
        props = pika.BasicProperties(
            delivery_mode=2,
            expiration=str(expiration_ms),
            headers={"batch_id": batch_id} if batch_id else {},
        )
        self.channel.basic_publish(
            exchange=self.EXCHANGE, routing_key=rk_retry,
            body=body, properties=props,
        )

    def consume(self, queue: str, callback, prefetch_count: int = 1):
        self._ensure()
        self.channel.basic_qos(prefetch_count=prefetch_count)

        def _callback(ch, method, properties, body):
            try:
                message = json.loads(body)
                message["_delivery_tag"] = method.delivery_tag
                message["_routing_key"] = method.routing_key
                message["_headers"] = (properties.headers or {})
                callback(message)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except _NackOnce as e:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=e.requeue)
            except Exception:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        self.channel.basic_consume(queue=queue, on_message_callback=_callback)

    def consume_dead(self, batch_id: str) -> list:
        """非消费式拉取 dead.letter 内匹配 batch_id 的死信(basic_get auto_ack)。

        auto_ack=True 把拉出的消息从 DLQ 移除;仅返回 header 匹配 batch_id 的条目。
        """
        self._ensure()
        out = []
        while True:
            method, props, body = self.channel.basic_get(queue=self.DEAD_LETTER_QUEUE, auto_ack=True)
            if method is None:
                break
            headers = (props.headers or {}) if props else {}
            entry = json.loads(body)
            entry["_headers"] = headers
            if headers.get("batch_id") == batch_id:
                out.append(entry)
        return out

    def start_consuming(self):
        self.channel.start_consuming()

    def close(self):
        if self.connection and self.connection.is_open:
            self.connection.close()


rabbitmq = RabbitMQClient()