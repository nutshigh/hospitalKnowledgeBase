"""service.py 集成测试 — 聚焦 worker 钩子 try_generate_comparison_summary。
无 DB 依赖,使用 sqlalchemy in-memory SQLite + mock LLM。
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportInfo, ReportIndicator
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment


@pytest.fixture
def db():
    """in-memory SQLite,创建所有表后 yield session。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _make_reports(db):
    """准备 2 份报告 + 指标,第二份(最新)打算解读完成时触发对比小结。"""
    db.add(ReportInfo(id=1, user_id=10, name="张三", gender="男", age=40,
                     report_date=date(2025, 11, 2)))
    db.add(ReportInfo(id=2, user_id=10, name="张三", gender="男", age=41,
                     report_date=date(2026, 6, 15)))
    db.add(ReportIndicator(report_id=1, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="7.2", unit="mmol/L"))
    db.add(ReportIndicator(report_id=2, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="6.8", unit="mmol/L"))
    db.commit()


def _make_completed_interpretation(db, report_id=2, baseline_id=1):
    """为 report_id=2 准备一个已 completed 的 interpretation(尚未生成小结)。"""
    db.add(ReportInterpretation(
        report_id=report_id, overall_level="yellow", status="completed",
        red_count=1, yellow_count=0, green_count=5,
    ))
    db.commit()


def test_try_generate_comparison_summary_writes_cache_on_first_call(db):
    """worker 钩子成功调 LLM 后,应写回 comparison_summary 与 comparison_baseline_id。"""
    _make_reports(db)
    _make_completed_interpretation(db)

    fake_model = MagicMock()
    fake_model.invoke.return_value = MagicMock(
        content="本次血糖从 7.2 降至 6.8,改善明显。" * 5
    )

    from app.modules.user_profile.service import try_generate_comparison_summary
    with patch("app.modules.user_profile.service.get_chat_model", return_value=fake_model):
        try_generate_comparison_summary(db, report_id=2)

    interp = db.query(ReportInterpretation).filter_by(report_id=2).first()
    assert interp.comparison_summary is not None
    assert "血糖" in interp.comparison_summary
    assert interp.comparison_baseline_id == 1


def test_try_generate_comparison_summary_skips_when_no_history_report(db):
    """用户只有 1 份报告时,base 缺失,worker 钩子不应抛错也不应写小结。"""
    db.add(ReportInfo(id=5, user_id=20, report_date=date(2026, 6, 15)))
    db.commit()
    db.add(ReportInterpretation(report_id=5, overall_level="green", status="completed"))
    db.commit()

    fake_model = MagicMock()
    from app.modules.user_profile.service import try_generate_comparison_summary
    with patch("app.modules.user_profile.service.get_chat_model", return_value=fake_model):
        try_generate_comparison_summary(db, report_id=5)

    interp = db.query(ReportInterpretation).filter_by(report_id=5).first()
    assert interp.comparison_summary is None
    assert interp.comparison_baseline_id is None
    fake_model.invoke.assert_not_called()


def test_try_generate_comparison_summary_swallows_llm_failure(db):
    """LLM 抛异常时,worker 钩子自己吃掉异常,不应冒泡。"""
    _make_reports(db)
    _make_completed_interpretation(db)

    fake_model = MagicMock()
    fake_model.invoke.side_effect = RuntimeError("LLM down")

    from app.modules.user_profile.service import try_generate_comparison_summary
    with patch("app.modules.user_profile.service.get_chat_model", return_value=fake_model):
        try_generate_comparison_summary(db, report_id=2)

    interp = db.query(ReportInterpretation).filter_by(report_id=2).first()
    assert interp.comparison_summary is None


def test_try_generate_comparison_summary_skips_when_cache_hit(db):
    """已有缓存且 baseline 一致 -> 跳过 LLM 调用。"""
    _make_reports(db)
    db.add(ReportInterpretation(
        report_id=2, overall_level="yellow", status="completed",
        red_count=1, yellow_count=0, green_count=5,
        comparison_summary="已缓存小结", comparison_baseline_id=1,
    ))
    db.commit()

    fake_model = MagicMock()
    from app.modules.user_profile.service import try_generate_comparison_summary
    with patch("app.modules.user_profile.service.get_chat_model", return_value=fake_model):
        try_generate_comparison_summary(db, report_id=2)

    interp = db.query(ReportInterpretation).filter_by(report_id=2).first()
    assert interp.comparison_summary == "已缓存小结"
    fake_model.invoke.assert_not_called()


def test_get_ai_summary_cache_hit_returns_cached_true(db):
    """comparison_summary 已存在且 baseline_id 匹配 -> cached=True。"""
    _make_reports(db)
    db.add(ReportInterpretation(
        report_id=2, overall_level="yellow", status="completed",
        red_count=1, yellow_count=0, green_count=5,
        comparison_summary="已缓存小结", comparison_baseline_id=1,
    ))
    db.commit()

    from app.modules.user_profile.service import get_ai_summary
    summary, cached = get_ai_summary(db, user_id=10, report_id=2, baseline_id=1)
    assert summary == "已缓存小结"
    assert cached is True


def test_get_ai_summary_calls_llm_when_baseline_mismatch(db):
    """缓存存在但 baseline 不匹配 -> 实时调 LLM 返回 cached=False,不写回缓存。"""
    _make_reports(db)
    db.add(ReportInterpretation(
        report_id=2, overall_level="yellow", status="completed",
        red_count=1, yellow_count=0, green_count=5,
        comparison_summary="针对旧基准的小结", comparison_baseline_id=1,
    ))
    db.commit()

    fake_model = MagicMock()
    fake_model.invoke.return_value = MagicMock(content="针对新基准的小结")

    from app.modules.user_profile.service import get_ai_summary
    with patch("app.modules.user_profile.service.get_chat_model", return_value=fake_model):
        summary, cached = get_ai_summary(db, user_id=10, report_id=2, baseline_id=2)

    assert "新基准" in summary
    assert cached is False
    interp = db.query(ReportInterpretation).filter_by(report_id=2).first()
    assert interp.comparison_summary == "针对旧基准的小结"