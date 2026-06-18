from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq
from app.ai.agents import run_interpretation_agent


def handle_interpretation_task(message: dict):
    payload = message.get("payload", {})
    if payload.get("event"):
        return
    report_id = payload.get("report_id")
    hospital_id = payload.get("hospital_id")

    if not report_id:
        return

    db = next(get_hospital_db(hospital_id))
    try:
        run_interpretation_agent(hospital_id, db, report_id)
    except Exception as e:
        print(f"Interpretation failed for report {report_id}: {e}")
    finally:
        db.close()


def start_worker():
    while True:
        try:
            rabbitmq.consume("interpretation.urgent", handle_interpretation_task)
            rabbitmq.consume("interpretation.normal", handle_interpretation_task)
            print("Interpretation worker started, waiting for tasks...")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Worker disconnected: {e}, reconnecting in 3s...")
            import time
            time.sleep(3)
