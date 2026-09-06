from datetime import datetime

import pytest
from sqlalchemy import BigInteger, create_engine, Integer
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportInfo, ReportIndicator  # noqa: F401
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment  # noqa: F401


def _swap_bigints(*tables):
    saved = []
    for tbl in tables:
        for c in tbl.c:
            if isinstance(c.type, BigInteger):
                saved.append((c, c.type))
                c.type = Integer()
    return saved


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    saved = _swap_bigints(
        ReportInfo.__table__, ReportIndicator.__table__,
        ReportInterpretation.__table__, IndicatorJudgment.__table__,
    )
    Base.metadata.create_all(engine)
    for c, t in saved:
        c.type = t
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_judgments_join_carries_indicator_category(db):
    from app.modules.report.models import ReportIndicator
    from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
    from app.modules.interpretation.service import get_judgments_with_indicator_detail

    ind1 = ReportIndicator(report_id=1, item_name="葡萄糖", result_value="阴性",
                           unit="mmol/L", category="尿常规")
    ind2 = ReportIndicator(report_id=1, item_name="血红蛋白", result_value="144",
                           unit="g/L", category=None)
    db.add_all([ind1, ind2]); db.commit(); db.refresh(ind1); db.refresh(ind2)

    interp = ReportInterpretation(report_id=1, status="completed", red_count=0,
                                  yellow_count=0, green_count=2,
                                  created_at=datetime(2026, 1, 1))
    db.add(interp); db.commit(); db.refresh(interp)
    db.add_all([
        IndicatorJudgment(interpretation_id=interp.id, indicator_id=ind1.id,
                          item_name="葡萄糖", result_value="阴性", color_level="green"),
        IndicatorJudgment(interpretation_id=interp.id, indicator_id=ind2.id,
                          item_name="血红蛋白", result_value="144", color_level="green"),
    ]); db.commit()

    rows = get_judgments_with_indicator_detail(db, interp.id)
    by_name = {r["item_name"]: r.get("category") for r in rows}
    assert by_name["葡萄糖"] == "尿常规"
    assert by_name["血红蛋白"] is None
