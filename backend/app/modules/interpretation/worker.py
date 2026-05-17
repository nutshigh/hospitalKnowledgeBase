from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq
from app.modules.interpretation.service import process_interpretation


def handle_interpretation_task(message: dict):
    payload = message.get("payload", {})
    report_id = payload.get("report_id")
    hospital_id = payload.get("hospital_id")

    if not report_id:
        return

    db = next(get_hospital_db(hospital_id))
    try:
        process_interpretation(db, report_id, hospital_id)
    finally:
        db.close()


def start_worker():
    rabbitmq.consume("interpretation.urgent", handle_interpretation_task)
    rabbitmq.consume("interpretation.normal", handle_interpretation_task)
    print("Interpretation worker started, waiting for tasks...")
    rabbitmq.start_consuming()
