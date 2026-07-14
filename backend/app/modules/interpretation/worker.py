import time

from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.ai.agents import run_interpretation_agent

_RETRY_BACKOFFS = (10, 20)


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
        try:
            from app.modules.user_profile.service import try_generate_comparison_summary
            try_generate_comparison_summary(db, report_id)
        except Exception as e:
            print(f"Comparison summary failed for report {report_id}: {e}", flush=True)
    except Exception as e:
        import traceback as _tb
        print(f"Interpretation failed for report {report_id}: {e}", flush=True)
        _tb.print_exc()
        _maybe_requeue_for_retry(hospital_id, db, report_id, e)
    finally:
        db.close()


def _maybe_requeue_for_retry(hospital_id: str, db, report_id: int, error: Exception) -> None:
    try:
        from app.modules.interpretation.models import ReportInterpretation
        interp = (
            db.query(ReportInterpretation)
            .filter(ReportInterpretation.report_id == report_id)
            .order_by(ReportInterpretation.id.desc())
            .first()
        )
    except Exception as qe:
        print(f"Retry lookup failed for report {report_id}: {qe}", flush=True)
        return

    if interp is None or interp.status != "pending":
        return

    retry = interp.retry_count or 0
    backoff_idx = max(retry - 1, 0)
    if backoff_idx >= len(_RETRY_BACKOFFS):
        return
    delay = _RETRY_BACKOFFS[backoff_idx]
    print(
        f"Re-enqueuing interpretation for report {report_id} "
        f"(attempt {retry + 1}/3) after {delay}s backoff. Last error: {error}",
        flush=True,
    )
    time.sleep(delay)
    try:
        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=hospital_id, priority=0,
            payload={"report_id": report_id, "hospital_id": hospital_id},
        ))
    except Exception as pub_err:
        print(f"Re-enqueue publish failed for report {report_id}: {pub_err}", flush=True)


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
