from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq
from app.modules.report.service import process_task


def handle_parsing_task(message: dict):
    payload = message.get("payload", {})
    task_id = payload.get("task_id")
    hospital_id = payload.get("hospital_id")

    db = next(get_hospital_db(hospital_id))
    try:
        from app.modules.report.service import get_task_status
        task = get_task_status(db, task_id)
        if task and task.status == "completed":
            print(f"Task {task_id} already completed, skipping")
            return
        process_task(db, task_id, hospital_id)
    finally:
        db.close()


def start_worker():
    while True:
        try:
            rabbitmq.consume("parsing.urgent", handle_parsing_task)
            rabbitmq.consume("parsing.normal", handle_parsing_task)
            print("Report parsing worker started, waiting for tasks...")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Worker disconnected: {e}, reconnecting in 3s...")
            import time
            time.sleep(3)
