"""验队列配置结构(不连真 RabbitMQ)。"""
from app.core.rabbitmq import RabbitMQClient


def test_queue_topology():
    c = RabbitMQClient
    assert set(c.QUEUES.values()) == {
        "parsing.urgent", "parsing.normal", "parsing.bulk",
        "interpretation.urgent", "interpretation.normal", "interpretation.bulk",
        "extract.bulk",
    }
    assert set(c.RETRY_QUEUES.keys()) == {
        "parsing.urgent.retry", "parsing.normal.retry", "parsing.bulk.retry",
        "interpretation.urgent.retry", "interpretation.normal.retry", "interpretation.bulk.retry",
        "extract.bulk.retry",
    }
    # 每个 retry 队列 DLX 回对应的原队列
    assert c.RETRY_QUEUES["parsing.bulk.retry"] == "parsing.bulk"
    assert c.RETRY_QUEUES["interpretation.bulk.retry"] == "interpretation.bulk"
    assert c.RETRY_QUEUES["extract.bulk.retry"] == "extract.bulk"
    assert c.DLX == "hospital.dlx"
    assert c.DEAD_LETTER_QUEUE == "dead.letter"


def test_routing_for_bulk_priority():
    """publish bulk 任务时 routing_key 应为 '<type>.bulk'."""
    from app.core.rabbitmq import TaskMessage
    # priority=2 -> bulk 扩展语义; 见 step 3 重新设计 priority 字段
    msg = TaskMessage(task_type="parsing", hospital_id="H", priority="bulk", payload={})
    rk = msg.routing_key()
    assert rk == "parsing.bulk"
    msg2 = TaskMessage(task_type="parsing", hospital_id="H", priority="urgent", payload={})
    assert msg2.routing_key() == "parsing.urgent"
    msg3 = TaskMessage(task_type="parsing", hospital_id="H", priority="normal", payload={})
    assert msg3.routing_key() == "parsing.normal"
    # 向后兼容: legacy int priority
    msg4 = TaskMessage(task_type="parsing", hospital_id="H", priority=1, payload={})
    assert msg4.routing_key() == "parsing.urgent"
    msg5 = TaskMessage(task_type="parsing", hospital_id="H", priority=0, payload={})
    assert msg5.routing_key() == "parsing.normal"
    msg6 = TaskMessage(task_type="extract", hospital_id="H", priority="bulk", payload={})
    assert msg6.routing_key() == "extract.bulk"