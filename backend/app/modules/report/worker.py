import json

from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq, _NackOnce
from app.core.retry import backoff_for_retry, is_bulk_window_now
from app.modules.report.service import process_task, get_task_status
from app.modules.report.batch_models import BatchImportFile
from app.modules.report.batch_service import BatchService


def handle_parsing_task(message: dict):
    routing_key = message.get("_routing_key", "parsing.normal")
    # bulk 时段过滤:非窗口期直接 requeue
    if routing_key.endswith(".bulk") and not is_bulk_window_now():
        raise _NackOnce(requeue=True)
    payload = message.get("payload", {})
    task_id = payload.get("task_id")
    hospital_id = payload.get("hospital_id")
    batch_id = payload.get("batch_id")
    file_id = payload.get("file_id")

    db = next(get_hospital_db(hospital_id))
    try:
        task = get_task_status(db, task_id)
        if task and task.status == "completed":
            return
        try:
            process_task(db, task_id, hospital_id, batch_id=batch_id, file_id=file_id)
            # 成功 → 计 batch file 进度(parsed_ok)
            if batch_id and file_id:
                BatchService.increment_progress(db, batch_id, file_id, "parsed_ok")
        except Exception:
            # 失败:由 process_task 已写 retry_count/status;此处按 retry_count 决策
            task = get_task_status(db, task_id)
            retries = task.retry_count if task else 0
            if retries >= 3:
                # 走 DLQ;同时回写 file failed
                if batch_id and file_id:
                    try:
                        BatchService.increment_progress(db, batch_id, file_id, "failed")
                    except Exception:
                        pass
                raise  # 让 _callback nack(requeue=False) → DLQ
            else:
                # 走延迟队列(TTL 后回流原 parsing.<priority>)
                body = json.dumps({
                    "task_type": "parsing",
                    "hospital_id": hospital_id,
                    "payload": payload,
                }).encode()
                rabbitmq.publish_retry(
                    routing_key, body,
                    expiration_ms=backoff_for_retry(retries - 1),
                    batch_id=batch_id,
                )
                return  # ack 当前消息(retry 队列里已存副本)
    finally:
        db.close()


def start_worker():
    while True:
        try:
            rabbitmq.consume("parsing.urgent", handle_parsing_task)
            rabbitmq.consume("parsing.normal", handle_parsing_task)
            rabbitmq.consume("parsing.bulk", handle_parsing_task)
            print("Report parsing worker started (urgent+normal+bulk)")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Parsing worker disconnected: {e}, reconnect in 3s")
            import time
            time.sleep(3)