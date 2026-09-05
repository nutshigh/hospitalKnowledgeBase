from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, Integer
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation  # noqa: F401
from app.modules.report.service import _build_parse_prompt


def _swap_int(*cols):
    saved = [(c, c.type) for c in cols]
    for c in cols:
        c.type = Integer()
    return saved


def _restore(saved):
    for c, t in saved:
        c.type = t


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    saved = _swap_int(
        ReportTask.__table__.c.id,
        ReportInfo.__table__.c.id,
        ReportIndicator.__table__.c.id,
    )
    Base.metadata.create_all(engine)
    _restore(saved)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _parse_mock(indicators):
    return (
        patch("app.modules.report.service._pdf_has_text", return_value=True),
        patch("app.modules.report.service._extract_pdf_text", return_value="体检文本"),
        patch("app.modules.report.service._parse_text_with_llm", return_value={
            "name": "测试", "gender": "男", "age": 30,
            "report_date": date(2026, 1, 1),
            "indicators": indicators,
        }),
        patch("app.modules.report.service.rabbitmq.publish"),
    )


def _process(db, indicators):
    from app.modules.report.service import process_task
    t = ReportTask(user_id="100001", original_file_path="/tmp/x.pdf",
                   original_filename="x.pdf", file_type="pdf", file_size=1,
                   status="queued", priority=0)
    db.add(t); db.commit(); db.refresh(t)
    db.add(ReportInfo(task_id=t.id, user_id="100001", name="测试"))
    db.commit()
    patchers = _parse_mock(indicators)
    for p in patchers:
        p.start()
    try:
        process_task(db, t.id, "1")
    finally:
        for p in patchers:
            p.stop()
    db.expire_all()
    return db.query(ReportIndicator).order_by(ReportIndicator.id).all()


def test_process_task_persists_valid_category(db):
    inds = _process(db, [
        {"item_name": "葡萄糖", "result": "阴性", "unit": "mmol/L",
         "ref_low": None, "ref_high": None, "category": "尿常规"},
        {"item_name": "白细胞", "result": "6.32", "unit": "10~9/L",
         "ref_low": None, "ref_high": None, "category": "血常规"},
    ])
    assert len(inds) == 2
    cat = {i.item_name: i.category for i in inds}
    assert cat["葡萄糖"] == "尿常规"
    assert cat["白细胞"] == "血常规"


def test_process_task_drops_invalid_category(db):
    inds = _process(db, [
        {"item_name": "葡萄糖", "result": "5.7", "unit": "mmol/L",
         "ref_low": None, "ref_high": None, "category": "乱写栏目"},
    ])
    assert inds[0].category is None


def test_process_task_without_category_stays_null(db):
    inds = _process(db, [
        {"item_name": "血红蛋白", "result": "144", "unit": "g/L",
         "ref_low": None, "ref_high": None},
    ])
    assert inds[0].category is None


def test_build_parse_prompt_has_category_and_hints():
    p = _build_parse_prompt("体检文本")
    assert '"category"' in p
    assert "尿常规" in p and "血常规" in p
    assert "null" in p
