"""service.py 集成测试。无 DB 依赖,使用 sqlalchemy in-memory SQLite。"""
import pytest
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


# ============================================================
# get_overview 走势限流(2026-09-08):indicator_trends 只取最近
# PROFILE_TREND_REPORT_LIMIT(默认3)份报告;user_summary/abnormal_distribution 仍全量
# ============================================================


def test_get_overview_trends_only_include_recent_n_reports(db):
    """5 份报告(2022..2026)→ 走势只含最近 3 份(2024/2025/2026)。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import IndicatorJudgment

    for rid, dt, val in [
        (1, date(2022, 5, 1), "5.5"),
        (2, date(2023, 5, 1), "6.0"),
        (3, date(2024, 5, 1), "6.5"),
        (4, date(2025, 5, 1), "7.0"),
        (5, date(2026, 5, 1), "7.4"),
    ]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
        db.add(ReportIndicator(id=rid * 100, report_id=rid, item_name="血糖", item_name_standard="空腹血糖",
                               result_value=val, unit="mmol/L"))
    db.commit()
    db.add(IndicatorJudgment(interpretation_id=99, indicator_id=500, item_name="血糖", color_level="red"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "空腹血糖")
    assert [p["report_date"] for p in trend["points"]] == ["2024-05-01", "2025-05-01", "2026-05-01"]
    assert result["user_summary"]["total_reports"] == 5


def test_get_overview_trends_keep_all_when_fewer_than_limit(db):
    """只有 2 份(< 默认3)时走势仍含全部,行为与现状一致。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import IndicatorJudgment

    db.add(ReportInfo(id=1, user_id="123456", name="张三", report_date=date(2025, 5, 1)))
    db.add(ReportInfo(id=2, user_id="123456", name="张三", report_date=date(2026, 5, 1)))
    db.add(ReportIndicator(id=100, report_id=1, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="6.0", unit="mmol/L"))
    db.add(ReportIndicator(id=200, report_id=2, item_name="血糖", item_name_standard="空腹血糖",
                           result_value="6.8", unit="mmol/L"))
    db.commit()
    db.add(IndicatorJudgment(interpretation_id=99, indicator_id=200, item_name="血糖", color_level="red"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "空腹血糖")
    assert len(trend["points"]) == 2


def test_get_overview_trends_exclude_null_dated_report(db):
    """6 份(5 有日期 + 1 无日期)→ 走势为最近 3 份有日期的,无日期那份垫最旧被排除。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import IndicatorJudgment

    for rid, dt in [(1, date(2022, 5, 1)), (2, date(2023, 5, 1)), (3, date(2024, 5, 1)),
                    (4, date(2025, 5, 1)), (5, date(2026, 5, 1))]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
    db.add(ReportInfo(id=6, user_id="123456", name="张三", report_date=None))
    for rid in range(1, 7):
        db.add(ReportIndicator(id=rid * 100, report_id=rid, item_name="血糖", item_name_standard="空腹血糖",
                               result_value="7.0", unit="mmol/L"))
    db.commit()
    db.add(IndicatorJudgment(interpretation_id=99, indicator_id=500, item_name="血糖", color_level="red"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "空腹血糖")
    dates = [p["report_date"] for p in trend["points"]]
    assert dates == ["2024-05-01", "2025-05-01", "2026-05-01"]
    assert None not in dates


def test_get_overview_abnormal_distribution_includes_outside_trend_window(db):
    """最旧报告(2023,在走势窗口外)有红判定 → abnormal_distribution 仍计入;走势不含它。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment

    for rid, dt in [(1, date(2023, 5, 1)), (2, date(2024, 5, 1)),
                    (3, date(2025, 5, 1)), (4, date(2026, 5, 1))]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
        db.add(ReportIndicator(id=100 + rid, report_id=rid, item_name="血糖",
                               item_name_standard="空腹血糖", result_value="7.0", unit="mmol/L"))
    db.commit()
    db.add(ReportInterpretation(id=1, report_id=1, overall_level="red", status="completed",
                                red_count=1, yellow_count=0, green_count=0))
    db.add(IndicatorJudgment(interpretation_id=1, indicator_id=101, item_name="血糖", color_level="red"))
    db.add(IndicatorJudgment(interpretation_id=4, indicator_id=104, item_name="血糖", color_level="yellow"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    assert any(a["item_name_standard"] == "空腹血糖" and a["red_count"] == 1
               for a in result["abnormal_distribution"])
    trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "空腹血糖")
    assert [p["report_date"] for p in trend["points"]] == ["2024-05-01", "2025-05-01", "2026-05-01"]


def test_get_overview_trends_hide_non_abnormal_in_window(db):
    """窗口(最近3份)内全绿/无判定/仅窗口外红 → 不展示;窗口内黄 → 展示。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import IndicatorJudgment

    # reports 1..5 = 2022..2026,窗口 = 3,4,5
    for rid, dt in [(1, date(2022, 5, 1)), (2, date(2023, 5, 1)), (3, date(2024, 5, 1)),
                    (4, date(2025, 5, 1)), (5, date(2026, 5, 1))]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
    # 收缩压:报告1(窗口外)红,报告3/4/5 绿 → 不应展示
    for rid in range(1, 6):
        db.add(ReportIndicator(id=rid * 10 + 1, report_id=rid, item_name="收缩压",
                               item_name_standard="收缩压", result_value="140", unit="mmHg"))
    # 空腹血糖:报告4(窗口内)黄,其余无判定 → 应展示
    for rid in range(1, 6):
        db.add(ReportIndicator(id=rid * 10 + 2, report_id=rid, item_name="血糖",
                               item_name_standard="空腹血糖", result_value="6.8", unit="mmol/L"))
    # 甘油三酯:全无判定 → 不应展示
    for rid in range(1, 6):
        db.add(ReportIndicator(id=rid * 10 + 3, report_id=rid, item_name="甘油三酯",
                               item_name_standard="甘油三酯", result_value="1.5", unit="mmol/L"))
    db.commit()
    db.add(IndicatorJudgment(interpretation_id=1, indicator_id=11, item_name="收缩压", color_level="red"))
    for rid in (3, 4, 5):
        db.add(IndicatorJudgment(interpretation_id=rid, indicator_id=rid * 10 + 1,
                                 item_name="收缩压", color_level="green"))
    db.add(IndicatorJudgment(interpretation_id=4, indicator_id=42, item_name="血糖", color_level="yellow"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    names = [t["item_name_standard"] for t in result["indicator_trends"]]
    assert names == ["空腹血糖"]  # 收缩压(窗口外红/窗口内绿)、甘油三酯(无判定)都被过滤


def test_get_overview_trends_order_red_before_yellow(db):
    """窗口内最近一次异常:红优先于黄。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import IndicatorJudgment

    for rid, dt in [(1, date(2025, 5, 1)), (2, date(2025, 11, 2)), (3, date(2026, 5, 1))]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
    # 指标 X 最近异常(报告3)黄,指标 Y 最近异常(报告3)红
    db.add(ReportIndicator(id=1, report_id=3, item_name="X", item_name_standard="X",
                           result_value="3.0", unit=""))
    db.add(ReportIndicator(id=2, report_id=2, item_name="X", item_name_standard="X",
                           result_value="2.0", unit=""))
    db.add(ReportIndicator(id=3, report_id=3, item_name="Y", item_name_standard="Y",
                           result_value="9.0", unit=""))
    db.add(ReportIndicator(id=4, report_id=2, item_name="Y", item_name_standard="Y",
                           result_value="8.0", unit=""))
    db.commit()
    db.add(IndicatorJudgment(interpretation_id=10, indicator_id=1, item_name="X", color_level="yellow"))
    db.add(IndicatorJudgment(interpretation_id=20, indicator_id=3, item_name="Y", color_level="red"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    names = [t["item_name_standard"] for t in result["indicator_trends"]]
    assert names == ["Y", "X"]


def test_get_overview_trends_retained_green_newest_sorts_by_abnormal_color(db):
    """隔离“最近异常点”排序语义:指标最新点转绿但更早已红 → 仍被保留,且其最近异常点(红)
    优先于“最新点为黄”的指标。若回归成按最新点颜色(latest_deviation)排序,A 会掉到 B 后。"""
    from app.modules.user_profile.service import get_overview
    from app.modules.interpretation.models import IndicatorJudgment

    for rid, dt in [(1, date(2024, 5, 1)), (2, date(2025, 5, 1)), (3, date(2026, 5, 1))]:
        db.add(ReportInfo(id=rid, user_id="123456", name="张三", report_date=dt))
    # 指标 A:3 份报告都有点,报告2 红、报告3(最新)绿 → 仍须展示且按最近异常点“红”排
    for rid, val in [(1, "6.0"), (2, "8.0"), (3, "5.8")]:
        db.add(ReportIndicator(id=100 + rid, report_id=rid, item_name="A", item_name_standard="A",
                               result_value=val, unit="mmol/L"))
    # 指标 B:3 份报告都有点,报告3(最新)黄
    for rid, val in [(1, "6.0"), (2, "6.2"), (3, "7.0")]:
        db.add(ReportIndicator(id=200 + rid, report_id=rid, item_name="B", item_name_standard="B",
                               result_value=val, unit="mmol/L"))
    db.commit()
    db.add(IndicatorJudgment(interpretation_id=99, indicator_id=102, item_name="A", color_level="red"))
    db.add(IndicatorJudgment(interpretation_id=99, indicator_id=103, item_name="A", color_level="green"))
    db.add(IndicatorJudgment(interpretation_id=99, indicator_id=203, item_name="B", color_level="yellow"))
    db.commit()

    result = get_overview(db, user_id="123456", name="张三")
    a_trend = next(t for t in result["indicator_trends"] if t["item_name_standard"] == "A")
    assert a_trend["latest_deviation"] == "green"  # A 最新点确实是绿,测试才具区分度
    names = [t["item_name_standard"] for t in result["indicator_trends"]]
    assert names == ["A", "B"]
