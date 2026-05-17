import psutil
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.rabbitmq import rabbitmq


def get_resource_metrics() -> dict:
    cpu = psutil.cpu_percent(interval=0.1)
    memory = psutil.virtual_memory().percent

    parsing_depth = 0
    interp_depth = 0
    try:
        if rabbitmq.channel and rabbitmq.channel.is_open:
            parsing_urgent = rabbitmq.channel.queue_declare(queue="parsing.urgent", passive=True)
            parsing_normal = rabbitmq.channel.queue_declare(queue="parsing.normal", passive=True)
            interp_urgent = rabbitmq.channel.queue_declare(queue="interpretation.urgent", passive=True)
            interp_normal = rabbitmq.channel.queue_declare(queue="interpretation.normal", passive=True)
            parsing_depth = parsing_urgent.method.message_count + parsing_normal.method.message_count
            interp_depth = interp_urgent.method.message_count + interp_normal.method.message_count
    except Exception:
        pass

    return {
        "cpu_percent": cpu,
        "memory_percent": memory,
        "gpu_percent": None,
        "gpu_memory_percent": None,
        "queue_depth_parsing": parsing_depth,
        "queue_depth_interpretation": interp_depth,
        "active_workers": 0,
    }


def get_queue_status() -> List[dict]:
    queues = []
    try:
        if rabbitmq.channel and rabbitmq.channel.is_open:
            for q in rabbitmq.QUEUES.values():
                result = rabbitmq.channel.queue_declare(queue=q, passive=True)
                queues.append({
                    "queue_name": q,
                    "depth": result.method.message_count,
                    "consumer_count": result.method.consumer_count,
                })
    except Exception:
        pass
    return queues


def get_config(db: Session) -> dict:
    rows = db.execute(
        text("SELECT config_key, config_value FROM dispatch_config")
    ).fetchall()
    return {r.config_key: r.config_value for r in rows}


def update_config(db: Session, updates: dict) -> dict:
    for key, value in updates.items():
        if value is not None:
            db.execute(
                text("INSERT INTO dispatch_config (config_key, config_value) VALUES (:k, :v) "
                     "ON DUPLICATE KEY UPDATE config_value = :v"),
                {"k": key, "v": str(value)},
            )
    db.commit()
    return get_config(db)
