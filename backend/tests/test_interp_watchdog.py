"""解读看门狗单测(2026-09-13)。

背景: report 31 因 30min SDK 重试放大 + RabbitMQ consumer_timeout, 消息丢失后
report_interpretation 永久停在 processing/pending 无人处理。看门狗周期巡检陈旧行,
重置为 pending 并重新投递解读任务(先重置是因为 worker running-skip 会跳过
processing/completed)。
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.modules.report.models import ReportInfo, ReportTask, ReportIndicator  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation
from app.core import interp_watchdog as wd


@pytest.fixture
def engine():
    e = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def patched(engine):
    Session = sessionmaker(bind=engine)

    def _gen(hospital_id):
        s = Session()
        try:
            yield s
        finally:
            s.close()

    p_db = patch.object(wd, "get_hospital_db", side_effect=_gen)
    p_ids = patch.object(wd, "get_all_hospital_ids", return_value=["H001"])
    p_mq = patch.object(wd, "rabbitmq")
    p_db.start()
    p_ids.start()
    mq = p_mq.start()
    wd._nudged.clear()
    s = Session()
    try:
        yield s, mq
    finally:
        s.close()
        p_db.stop()
        p_ids.stop()
        p_mq.stop()
        wd._nudged.clear()


def _past(seconds):
    return datetime.utcnow() - timedelta(seconds=seconds)


def _add(db, report_id, status, age_s, row_id=None):
    row = ReportInterpretation(id=row_id or report_id, report_id=report_id, status=status)
    row.created_at = _past(age_s)
    db.add(row)
    db.commit()
    return row


def test_nudges_stale_processing(patched):
    db, mq = patched
    row = _add(db, 31, "processing", 2000)

    wd._sweep_once()

    db.refresh(row)
    assert row.status == "pending"
    assert mq.publish.call_count == 1
    msg = mq.publish.call_args.args[0]
    assert msg.task_type == "interpretation"
    assert msg.hospital_id == "H001"
    assert msg.payload["report_id"] == 31


def test_republishes_stale_pending(patched):
    db, mq = patched
    _add(db, 32, "pending", 2000)

    wd._sweep_once()

    assert mq.publish.call_count == 1
    assert mq.publish.call_args.args[0].payload["report_id"] == 32


def test_skips_fresh_row(patched):
    db, mq = patched
    row = _add(db, 33, "processing", 10)

    wd._sweep_once()

    db.refresh(row)
    assert row.status == "processing"
    assert mq.publish.call_count == 0


def test_skips_when_completed_exists(patched):
    db, mq = patched
    _add(db, 34, "processing", 2000, row_id=340)
    done = ReportInterpretation(id=341, report_id=34, status="completed")
    db.add(done)
    db.commit()

    wd._sweep_once()

    assert mq.publish.call_count == 0


def test_no_duplicate_nudge_within_threshold(patched):
    db, mq = patched
    _add(db, 35, "processing", 2000)

    wd._sweep_once()
    wd._sweep_once()

    assert mq.publish.call_count == 1


def test_publish_failure_does_not_abort_sweep(patched):
    db, mq = patched
    _add(db, 36, "processing", 2000)
    _add(db, 37, "processing", 2000)
    mq.publish.side_effect = RuntimeError("broker down")

    wd._sweep_once()  # 不抛异常

    assert mq.publish.call_count == 2
