"""service.py 集成测试 — 聚焦 worker 钩子 try_generate_comparison_summary。
无 DB 依赖,使用 sqlalchemy in-memory SQLite + mock LLM。
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
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
    db.add(ReportInfo(id=1, user_id="123456", name="张三", gender="男", age=40,
                     report_date=date(2025, 11, 2)))
    db.add(ReportInfo(id=2, user_id="123456", name="张三", gender="男", age=41,
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
    fake_model.ainvoke = AsyncMock(return_value=MagicMock(
        content="本次血糖从 7.2 降至 6.8,改善明显。" * 5
    ))

    from app.modules.user_profile.service import try_generate_comparison_summary
    with patch("app.modules.user_profile.service.get_chat_model", return_value=fake_model):
        try_generate_comparison_summary(db, report_id=2)

    interp = db.query(ReportInterpretation).filter_by(report_id=2).first()
    assert interp.comparison_summary is not None
    assert "血糖" in interp.comparison_summary
    assert interp.comparison_baseline_id == 1


def test_try_generate_comparison_summary_skips_when_no_history_report(db):
    """用户只有 1 份报告时,base 缺失,worker 钩子不应抛错也不应写小结。"""
    db.add(ReportInfo(id=5, user_id="123457", report_date=date(2026, 6, 15)))
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
    fake_model.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))

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
    summary, cached = get_ai_summary(db, user_id="123456", name="张三", report_id=2, baseline_id=1)
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
    fake_model.ainvoke = AsyncMock(return_value=MagicMock(content="针对新基准的小结"))

    from app.modules.user_profile.service import get_ai_summary
    with patch("app.modules.user_profile.service.get_chat_model", return_value=fake_model):
        summary, cached = get_ai_summary(db, user_id="123456", name="张三", report_id=2, baseline_id=2)

    assert "新基准" in summary
    assert cached is False
    interp = db.query(ReportInterpretation).filter_by(report_id=2).first()
    assert interp.comparison_summary == "针对旧基准的小结"


def test_get_overview_returns_empty_when_no_reports(db):
    """无报告时返回空结构(避免 get_overview 在 router 层崩)。"""
    from app.modules.user_profile.service import get_overview
    result = get_overview(db, user_id="999999", name="张三")
    assert result["user_summary"] is None
    assert result["indicator_trends"] == []
    assert result["abnormal_distribution"] == []


def test_get_overview_aggregates_abnormal_by_item_name_standard(db):
    """get_overview 应通过 JOIN report_indicator 按 item_name_standard 聚合异常指标(覆盖 service.py:89-96 的修复)。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2025, 11, 2)))
    db.add(ReportInfo(id=2, user_id="123456", name="张三", report_date=date(2026, 6, 15)))
    db.add(ReportIndicator(id=100, report_id=1, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="7.2", unit="mmol/L"))
    db.add(ReportIndicator(id=200, report_id=2, item_name="GLU", item_name_standard="空腹血糖",
                           result_value="6.8", unit="mmol/L"))
    db.commit()

    db.add(ReportInterpretation(id=1, report_id=1, overall_level="red", status="completed",
                                red_count=1, yellow_count=0, green_count=5))
    db.add(ReportInterpretation(id=2, report_id=2, overall_level="yellow", status="completed",
                                red_count=1, yellow_count=0, green_count=6))
    db.add(IndicatorJudgment(interpretation_id=1, indicator_id=100, item_name="血糖",
                             color_level="red"))
    db.add(IndicatorJudgment(interpretation_id=2, indicator_id=200, item_name="GLU",
                             color_level="red"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")

    assert result["user_summary"]["total_reports"] == 2
    assert result["user_summary"]["latest_overall_level"] == "yellow"
    assert len(result["indicator_trends"]) >= 1
    blood_trend = next((t for t in result["indicator_trends"]
                        if t["item_name_standard"] == "空腹血糖"), None)
    assert blood_trend is not None
    assert len(blood_trend["points"]) == 2
    assert blood_trend["trend_direction"] == "down"
    assert blood_trend["latest_deviation"] == "red"

    assert len(result["abnormal_distribution"]) == 1
    abnormal = result["abnormal_distribution"][0]
    assert abnormal["item_name_standard"] == "空腹血糖"
    assert abnormal["red_count"] == 2
    assert abnormal["yellow_count"] == 0
    assert abnormal["last_color"] == "red"


def test_get_comparison_does_not_double_count_baseline_with_diff_raw_names(db):
    """当两边 item_name 不同但 item_name_standard 一致时,
    基准侧的不应被误归为 only_in_baseline(原始 brief 的真实 bug,
    修复后必须:出现在 indicators 里,不出现在 only_in_baseline)。
    """
    from app.modules.user_profile.service import get_comparison

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2025, 11, 2)))
    db.add(ReportInfo(id=2, user_id="123456", name="张三", report_date=date(2026, 6, 15)))
    db.add(ReportIndicator(report_id=1, item_name="GLU", item_name_standard="空腹血糖",
                           result_value="7.2", unit="mmol/L"))
    db.add(ReportIndicator(report_id=2, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="6.8", unit="mmol/L"))
    db.commit()

    result = get_comparison(db, user_id="123456", name="张三", report_id=2, baseline_id=1)
    matched_names = {ind["item_name"] for ind in result["indicators"]}
    assert "GLU" in matched_names or "血糖" in matched_names
    baseline_only_names = {ind["item_name"] for ind in result["only_in_baseline"]}
    assert "GLU" not in baseline_only_names, (
        f"Bug: baseline indicator with same item_name_standard but different raw "
        f"name was misclassified as only_in_baseline. only_in_baseline={baseline_only_names}"
    )


def test_get_overview_sorts_points_by_report_date(db):
    """即使 ReportInfo.id 与 report_date 反向,points 仍按 report_date 排序,
    latest_deviation 取到真正最新的报告日期对应的 color,趋势方向正确。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment

    # id=2 is OLDER, id=1 is NEWER — non-monotonic, the case the bug surfaces in
    db.add(ReportInfo(id=2, user_id="123458", name="李四", report_date=date(2025, 4, 10)))
    db.add(ReportInfo(id=1, user_id="123458", name="李四", report_date=date(2026, 6, 15)))
    db.add(ReportIndicator(id=500, report_id=2, item_name="血压", item_name_standard="收缩压",
                          result_value="130", unit="mmHg"))
    db.add(ReportIndicator(id=600, report_id=1, item_name="血压", item_name_standard="收缩压",
                          result_value="145", unit="mmHg"))
    db.commit()
    db.add(ReportInterpretation(id=10, report_id=2, overall_level="green", status="completed",
                                red_count=0, yellow_count=0, green_count=5))
    db.add(ReportInterpretation(id=20, report_id=1, overall_level="red", status="completed",
                                red_count=1, yellow_count=0, green_count=5))
    db.add(IndicatorJudgment(interpretation_id=20, indicator_id=600, item_name="血压", color_level="red"))
    db.commit()

    result = get_overview(db, user_id="123458", name="李四")
    sys_trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "收缩压")
    # Points MUST be in chronological order by report_date
    assert [p["report_date"] for p in sys_trend["points"]] == ["2025-04-10", "2026-06-15"]
    # Values following that order
    assert [p["value"] for p in sys_trend["points"]] == [130.0, 145.0]
    # Latest color from newest report_date (2026-06-15 → red)
    assert sys_trend["latest_deviation"] == "red"
    # Trend up (newer > older)
    assert sys_trend["trend_direction"] == "up"


def test_get_comparison_raises_not_found_when_report_missing(db):
    """report_id 不属于该 user 或不存在 -> NotFoundException,不是返回空dict。"""
    import pytest
    from app.utils.exceptions import NotFoundException

    from app.modules.user_profile.service import get_comparison
    with pytest.raises(NotFoundException):
        get_comparison(db, user_id="123456", name="张三", report_id=999, baseline_id=None)


def test_get_comparison_raises_validation_when_baseline_not_owned(db):
    """baseline_id 提供但不属于该 user -> ValidationException(400),不是404。"""
    import pytest
    from app.utils.exceptions import ValidationException

    from app.modules.user_profile.service import get_comparison

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2026, 6, 15)))
    db.add(ReportInfo(id=99, user_id="123457", name="张三", report_date=date(2025, 4, 10)))  # 属于另一 user
    db.commit()

    with pytest.raises(ValidationException):
        get_comparison(db, user_id="123456", name="张三", report_id=1, baseline_id=99)


def test_get_ai_summary_raises_validation_when_baseline_not_owned(db):
    """get_ai_summary 的 baseline 非本用户历史 -> ValidationException。"""
    import pytest
    from app.utils.exceptions import ValidationException

    from app.modules.user_profile.service import get_ai_summary

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2026, 6, 15)))
    db.add(ReportInfo(id=99, user_id="123457", name="张三", report_date=date(2025, 4, 10)))
    db.commit()

    with pytest.raises(ValidationException):
        get_ai_summary(db, user_id="123456", name="张三", report_id=1, baseline_id=99)


# ============================================================
# _auto_select_baseline 退化策略(2026-09-02):
# 当前报告为该用户最早一份(无更早 report_date)时,不再返回 None 导致对比卡片整体隐藏;
# 改为退化为该用户 report_date 日期最接近的另一份报告,使“最早一份”也能进入对比 UI。
# ============================================================


def test_auto_baseline_falls_back_to_later_report_when_current_is_earliest(db):
    """当前是最早一份(report 9 场景)→ 基线退化为日期最接近的另一份(允许选任意报告)。"""
    from datetime import datetime
    from app.modules.user_profile.service import _auto_select_baseline

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2025, 6, 20),
                      created_at=datetime(2026, 9, 2, 22, 23)))
    db.add(ReportInfo(id=2, user_id="123456", name="张三", report_date=date(2025, 6, 24),
                      created_at=datetime(2026, 9, 2, 13, 22)))
    db.add(ReportInfo(id=3, user_id="123456", name="张三", report_date=date(2026, 8, 30),
                      created_at=datetime(2026, 9, 2, 21, 38)))
    db.commit()

    baseline = _auto_select_baseline(db, "123456", "张三", report_id=1)
    assert baseline is not None
    assert baseline.id == 2  # |日期差| 最小(2025-06-24),不是日期最晚的 3


def test_auto_baseline_prefers_closest_earlier_report(db):
    """有更早报告时行为不变:取严格早于当前、日期最接近的那份。"""
    from app.modules.user_profile.service import _auto_select_baseline

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2025, 6, 20)))
    db.add(ReportInfo(id=2, user_id="123456", name="张三", report_date=date(2025, 11, 2)))
    db.add(ReportInfo(id=3, user_id="123456", name="张三", report_date=date(2026, 6, 15)))
    db.commit()

    baseline = _auto_select_baseline(db, "123456", "张三", report_id=3)
    assert baseline is not None
    assert baseline.id == 2  # 早于 2026-06-15 且最近(2025-11-02 > 2025-06-20)


def test_auto_baseline_none_when_only_one_report(db):
    """用户只有 1 份报告时仍返回 None(没有可比的其它报告,卡片继续隐藏)。"""
    from app.modules.user_profile.service import _auto_select_baseline

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2025, 6, 20)))
    db.commit()

    assert _auto_select_baseline(db, "123456", "张三", report_id=1) is None


def test_get_comparison_earliest_report_returns_baseline_and_diff(db):
    """get_comparison 对“最早一份”报告不再返回 baseline:None,而是给出基线与指标差异。"""
    from datetime import datetime
    from app.modules.user_profile.service import get_comparison

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2025, 6, 20),
                      created_at=datetime(2026, 9, 2, 22, 23)))
    db.add(ReportInfo(id=2, user_id="123456", name="张三", report_date=date(2025, 6, 24),
                      created_at=datetime(2026, 9, 2, 13, 22)))
    db.add(ReportIndicator(report_id=1, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="7.2", unit="mmol/L"))
    db.add(ReportIndicator(report_id=2, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="6.8", unit="mmol/L"))
    db.commit()

    result = get_comparison(db, user_id="123456", name="张三", report_id=1, baseline_id=None)
    assert result["baseline"] is not None
    assert result["baseline"]["report_id"] == 2
    assert len(result["indicators"]) == 1
    assert result["indicators"][0]["current_value"] == "7.2"
    assert result["indicators"][0]["baseline_value"] == "6.8"