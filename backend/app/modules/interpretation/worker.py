import json
import logging
import time

from app.core.database import get_hospital_db
from app.core.logging_config import setup_logging
from app.core.rabbitmq import rabbitmq, _NackOnce, TaskMessage
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
            # 解读已完成但结论异常还未提取 → 补跑异常提取
            if existing.status == "completed":
                try:
                    from app.modules.report.service import _extract_abnormalities_async, _store_abnormalities
                    from app.modules.report.models import ReportInfo
                    from sqlalchemy import text
                    report_info = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
                    if report_info and report_info.conclusion_text:
                        has_ab = db.execute(text(
                            "SELECT COUNT(*) FROM indicator_judgment ij JOIN report_indicator ri ON ij.indicator_id = ri.id WHERE ij.interpretation_id = :iid AND ri.raw_text IS NOT NULL"
                        ), {"iid": existing.id}).scalar()
                        if not has_ab:
                            import asyncio
                            items = asyncio.run(_extract_abnormalities_async(report_info.conclusion_text))
                            if items:
                                _store_abnormalities(db, report_id, existing.id, items)
                                _log.info("backfill abnormalities report=%d count=%d", report_id, len(items))
                except Exception as e:
                    _log.warning("backfill abnormalities failed report=%d: %s", report_id, e)
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
            # 从结论文本提取异常项 → 写入 indicator_judgment
            try:
                from app.modules.report.service import _extract_abnormalities_async, _store_abnormalities
                from app.modules.report.models import ReportInfo
                report_info = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
                if report_info and report_info.conclusion_text:
                    interp = db.query(ReportInterpretation).filter(
                        ReportInterpretation.report_id == report_id,
                        ReportInterpretation.status == "completed",
                    ).order_by(ReportInterpretation.id.desc()).first()
                    if interp:
                        import asyncio
                        items = asyncio.run(_extract_abnormalities_async(report_info.conclusion_text))
                        if items:
                            _store_abnormalities(db, report_id, interp.id, items)
                        # 刷新解释统计（含结论异常）
                        from app.modules.interpretation.service import refresh_interpretation_counts
                        refresh_interpretation_counts(db, interp.id)
            except Exception as e:
                _log.warning("abnormality extraction failed report=%d: %s", report_id, e)
            # 成功 → 计 batch file 进度(interp_ok)
            if batch_id and file_id:
                BatchService.increment_progress(db, batch_id, file_id, "interp_ok")
            # === STRATEGY:v2026-08-15-risk-hit 解读完成 → 异步触发病种命中计算 ===
            # 不阻塞本链路;失败仅记日志, 由 risk worker 侧 retry 兜底。
            # 回退: 删除本段即可。
            try:
                interp_row = db.query(ReportInterpretation).filter(
                    ReportInterpretation.report_id == report_id,
                    ReportInterpretation.status == "completed",
                ).order_by(ReportInterpretation.id.desc()).first()
                rabbitmq.publish(TaskMessage(
                    task_type="risk",
                    hospital_id=hospital_id,
                    priority="normal",
                    payload={
                        "report_id": report_id,
                        "interpretation_id": interp_row.id if interp_row else None,
                    },
                ))
            except Exception as e:
                _log.warning("risk publish failed report=%s: %s", report_id, e)
            # === END STRATEGY ===
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
                        BatchService.increment_progress(db, batch_id, file_id, "failed", stage="interpretation")
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