import json
import logging
import time

from app.core.database import get_hospital_db
from app.core.logging_config import setup_logging
from app.core.rabbitmq import rabbitmq, _NackOnce
from app.core.retry import backoff_for_retry, is_bulk_window_now
from app.ai.agents import run_interpretation_agent
from app.modules.report.batch_service import BatchService

_log = logging.getLogger("app.interp.worker")


def handle_interpretation_task(message: dict):
    routing_key = message.get("_routing_key", "interpretation.normal")
    # bulk 时段过滤:非窗口期直接 requeue
    if routing_key.endswith(".bulk") and not is_bulk_window_now():
        raise _NackOnce(requeue=True)
    payload = message.get("payload", {})
    if payload.get("event"):
        return
    report_id = payload.get("report_id")
    hospital_id = payload.get("hospital_id")
    batch_id = payload.get("batch_id")
    file_id = payload.get("file_id")
    batch_hospital_id = payload.get("batch_hospital_id")

    if not report_id:
        return

    db = next(get_hospital_db(hospital_id))
    try:
        # F15 running-skip:已存在 processing/completed 的解读行 → ack 跳过,
        # 避免重复投递并发跑两次 agent / 抢同一行。
        from app.modules.interpretation.models import ReportInterpretation
        existing = (
            db.query(ReportInterpretation)
            .filter(
                ReportInterpretation.report_id == report_id,
                ReportInterpretation.status.in_(("processing", "completed")),
            )
            .first()
        )
        if existing:
            _log.debug("interp skip (already running/completed) report=%s hospital=%s", report_id, hospital_id)
            return  # ack and skip — another worker is/has handled this report
        t_start = time.time()
        try:
            run_interpretation_agent(hospital_id, db, report_id)
            latency_ms = int((time.time() - t_start) * 1000)
            _log.info(
                "interp ok report=%s hospital=%s batch=%s file=%s latency_ms=%d",
                report_id, hospital_id, batch_id, file_id, latency_ms,
            )
            # register comparison summary(failures don't affect interp completion)
            try:
                from app.modules.user_profile.service import (
                    try_generate_comparison_summary,
                )
                try_generate_comparison_summary(db, report_id)
            except Exception as e:
                print(
                    f"Comparison summary failed for report {report_id}: {e}",
                    flush=True,
                )
            # 成功 → 计 batch file 进度(interp_ok),落在批次所属库
            if batch_id and file_id:
                BatchService.update_batch_progress(
                    batch_hospital_id, hospital_id, db, batch_id, file_id, "interp_ok")
        except Exception as e:
            latency_ms = int((time.time() - t_start) * 1000)
            _log.warning(
                "interp fail report=%s hospital=%s batch=%s file=%s latency_ms=%d: %s",
                report_id, hospital_id, batch_id, file_id, latency_ms, e,
            )
            import traceback as _tb
            print(f"Interpretation failed for report {report_id}: {e}", flush=True)
            _tb.print_exc()
            # 失败:run_interpretation_agent 已写 retry_count/status;此处按 retry_count 决策
            from app.modules.interpretation.models import ReportInterpretation
            interp = (
                db.query(ReportInterpretation)
                .filter(ReportInterpretation.report_id == report_id)
                .order_by(ReportInterpretation.id.desc())
                .first()
            )
            retries = interp.retry_count if interp else 0
            if retries >= 3:
                # 走 DLQ;同时回写 file failed
                if batch_id and file_id:
                    try:
                        BatchService.update_batch_progress(
                            batch_hospital_id, hospital_id, db, batch_id, file_id,
                            "failed", stage="interpretation")
                    except Exception:
                        pass
                raise  # 让 _callback nack(requeue=False) → DLQ
            else:
                # 走延迟队列(TTL 后回流原 interpretation.<priority>)
                body = json.dumps({
                    "task_type": "interpretation",
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
    setup_logging()
    while True:
        try:
            rabbitmq.consume("interpretation.urgent", handle_interpretation_task)
            rabbitmq.consume("interpretation.normal", handle_interpretation_task)
            rabbitmq.consume("interpretation.bulk", handle_interpretation_task)
            print("Interpretation worker started (urgent+normal+bulk)")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Worker disconnected: {e}, reconnecting in 3s...")
            import time
            time.sleep(3)