"""解读看门狗(2026-09-13): 巡检并拯救卡死在 processing/pending 的解读行。

背景(report 31 事故): MedGo 600s 超时被 openai SDK 默认 max_retries=2 放大到
≈30min, 撞上 RabbitMQ consumer_timeout(30min) → broker 关 channel、重试消息发不出去
→ report_interpretation 永久停在 processing/pending, 无人再处理。

看门狗周期扫描各租户库中"陈旧且无进展"的解读行, **先重置为 pending** 再重新投递
解读任务。必须先重置: `handle_interpretation_task` 的 running-skip 会跳过
status in (processing, completed) 的重复投递。
"""
import asyncio
import logging
from datetime import datetime, timedelta

from app.config import settings
from app.core.database import get_all_hospital_ids, get_hospital_db
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.modules.interpretation.models import ReportInterpretation

log = logging.getLogger("app.interp.watchdog")

# (hospital_id, report_id) -> 上次 nudge 时间, 防止同一行在阈值窗口内被反复重投。
_nudged: dict = {}


def _stale_rows(db, threshold):
    return (
        db.query(ReportInterpretation)
        .filter(
            ReportInterpretation.status.in_(("processing", "pending")),
            ReportInterpretation.created_at < threshold,
        )
        .all()
    )


def _has_completed(db, report_id: int) -> bool:
    return (
        db.query(ReportInterpretation.id)
        .filter(
            ReportInterpretation.report_id == report_id,
            ReportInterpretation.status == "completed",
        )
        .first()
        is not None
    )


def _nudge(db, hospital_id: str, row, now: datetime) -> None:
    prev_status = row.status
    row.status = "pending"
    db.commit()
    rabbitmq.publish(TaskMessage(
        task_type="interpretation",
        hospital_id=hospital_id,
        priority="normal",
        payload={"report_id": row.report_id, "hospital_id": hospital_id},
    ))
    _nudged[(hospital_id, row.report_id)] = now
    age_s = int((now - row.created_at).total_seconds()) if row.created_at else -1
    log.warning(
        "interp watchdog nudge report=%s hospital=%s was_status=%s retry_count=%s age_s=%s",
        row.report_id, hospital_id, prev_status, row.retry_count, age_s,
    )


def _sweep_hospital(db, hospital_id: str, threshold, stall: timedelta,
                    now: datetime) -> None:
    for row in _stale_rows(db, threshold):
        if _has_completed(db, row.report_id):
            continue
        last = _nudged.get((hospital_id, row.report_id))
        if last is not None and (now - last) < stall:
            continue
        try:
            _nudge(db, hospital_id, row, now)
        except Exception:
            db.rollback()
            log.exception(
                "interp watchdog nudge failed report=%s hospital=%s",
                row.report_id, hospital_id,
            )


def _prune_nudged(now: datetime, stall: timedelta) -> None:
    for key, ts in list(_nudged.items()):
        if (now - ts) > stall * 2:
            _nudged.pop(key, None)


def _sweep_once(now: datetime | None = None) -> None:
    now = now or datetime.utcnow()
    stall = timedelta(seconds=settings.INTERP_WATCHDOG_STALL_THRESHOLD)
    threshold = now - stall
    _prune_nudged(now, stall)
    for hospital_id in get_all_hospital_ids():
        try:
            db = next(get_hospital_db(hospital_id))
        except Exception:
            log.exception("interp watchdog open db failed hospital=%s", hospital_id)
            continue
        try:
            _sweep_hospital(db, hospital_id, threshold, stall, now)
        except Exception:
            log.exception("interp watchdog sweep failed hospital=%s", hospital_id)
        finally:
            db.close()


async def start():
    """后台协程, 周期巡检卡死的解读行。"""
    if not settings.INTERP_WATCHDOG_ENABLED:
        log.info("interp watchdog disabled")
        return
    while True:
        try:
            await asyncio.sleep(settings.INTERP_WATCHDOG_INTERVAL)
            _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("interp watchdog sweep error: %s", e)
