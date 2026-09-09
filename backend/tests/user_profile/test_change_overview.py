"""service.get_change_overview / ensure_change_overview 集成测试(SQLite + mock LLM)。"""
import pytest
from datetime import date
from unittest.mock import patch, MagicMock, AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.modules.report.models import ReportInfo, ReportIndicator
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _report(db, rid, user="123456", name="张三", rdate=None):
    db.add(ReportInfo(id=rid, user_id=user, name=name, report_date=rdate))


def _indicator(db, iid, rid, name, std, val, unit="mmol/L"):
    db.add(ReportIndicator(id=iid, report_id=rid, item_name=name,
                           item_name_standard=std, result_value=val, unit=unit))


def _completed(db, rid, level="green", red=0, yellow=0, green=0, status="completed",
               extra=None):
    kw = dict(report_id=rid, overall_level=level, status=status,
              red_count=red, yellow_count=yellow, green_count=green)
    if extra:
        kw.update(extra)
    db.add(ReportInterpretation(id=rid, **kw))


def _judgment(db, jid, rid, iid, color):
    db.add(IndicatorJudgment(id=jid, interpretation_id=rid, indicator_id=iid,
                             item_name="x", color_level=color))


def _fake_model(content):
    m = MagicMock()
    m.ainvoke = AsyncMock(return_value=MagicMock(content=content))
    return m


_JSON_OK = ('{"trend_summary": "红区略降", "conclusion": "血糖回落", '
            '"suggestions": "继续控糖", "precautions": "定期复查"}')

_model_patch = "app.modules.user_profile.service.get_chat_model"


# ---------- 取窗 ----------

def test_window_only_completed_and_recent_three(db):
    """未解读/processing 不入窗;completed 里按 report_date 取最近 3 份。"""
    from app.modules.user_profile.service import _change_window
    for rid, dt in [(1, date(2022, 5, 1)), (2, date(2023, 5, 1)),
                    (3, date(2024, 5, 1)), (4, date(2025, 5, 1))]:
        _report(db, rid, rdate=dt)
    # rid=3 只有 processing 占位,rid=4 completed,rid=1/2 completed
    _completed(db, 1, level="green")
    _completed(db, 2, level="yellow")
    _completed(db, 3, status="processing")
    _completed(db, 4, level="red", red=1)
    db.commit()

    window = _change_window(db, "123456", "张三")
    ids = [r.id for r, _ in window]
    assert ids == [1, 2, 4]  # 3(processing)被排除;取最近 3 份 completed


def test_window_null_date_treated_oldest(db):
    """report_date=None 垫最旧:有足够有日期报告时不入窗。"""
    from app.modules.user_profile.service import _change_window
    for rid, dt in [(1, date(2022, 5, 1)), (2, date(2023, 5, 1)),
                    (3, date(2024, 5, 1))]:
        _report(db, rid, rdate=dt)
    _report(db, 9, rdate=None)
    for rid in (1, 2, 3, 9):
        _completed(db, rid)
    db.commit()

    ids = [r.id for r, _ in _change_window(db, "123456", "张三")]
    assert ids == [1, 2, 3]
    assert 9 not in ids


# ---------- 降级 ----------

def test_insufficient_single_report_degrades_without_llm(db):
    """仅 1 份 completed → 降级响应,不调 LLM。"""
    from app.modules.user_profile.service import get_change_overview
    _report(db, 1, rdate=date(2026, 5, 1))
    _completed(db, 1)
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    assert result["reason"] == "insufficient"
    assert result["covered"] == 1
    assert result["summary"] is None
    m.return_value.ainvoke.assert_not_called()


# ---------- 正常生成 + 缓存 ----------

def test_generate_writes_cache_and_second_call_hits(db):
    """首次生成 summary 且 cached=False;二次读取 cached=True 且不调 LLM。"""
    from app.modules.user_profile.service import get_change_overview
    for rid, val in [(1, "7.2"), (2, "6.4")]:
        _report(db, rid, rdate=date(2025, 5, rid))
        _indicator(db, rid, rid, "血糖", "空腹血糖", val)
        _completed(db, rid, level="yellow", yellow=1)
    _judgment(db, 1001, 1, 1, "red")
    _judgment(db, 1002, 2, 2, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        first = get_change_overview(db, "123456", "张三")
    assert first["cached"] is False
    assert first["covered"] == 2
    assert first["summary"]["conclusion"] == "血糖回落"
    assert len(first["key_indicators"]) == 1

    with patch(_model_patch) as m:
        m.return_value = _fake_model("should not be called")
        second = get_change_overview(db, "123456", "张三")
    assert second["cached"] is True
    assert second["summary"] == first["summary"]
    m.return_value.ainvoke.assert_not_called()


def test_stale_plain_text_cache_regenerates(db):
    """旧列里是历史纯文本(非 JSON)→ 视为失效重算并覆盖,返回 cached=False。"""
    from app.modules.user_profile.service import get_change_overview
    for rid, val in [(1, "7.2"), (2, "6.4")]:
        _report(db, rid, rdate=date(2025, 5, rid))
        _indicator(db, rid, rid, "血糖", "空腹血糖", val)
        _completed(db, rid, extra={"comparison_summary": "旧双报告纯文本小结"})
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    assert result["cached"] is False
    assert result["summary"]["conclusion"] == "血糖回落"
    stored = db.query(ReportInterpretation).filter_by(report_id=2).first()
    import json as _json
    payload = _json.loads(stored.comparison_summary)["payload"]
    assert payload["summary"]["conclusion"] == "血糖回落"


def test_stale_after_new_report_changes_window(db):
    """新增更近 completed 报告后窗口变化 → 换锚点重算。"""
    from app.modules.user_profile.service import get_change_overview
    for rid, val in [(1, "7.2"), (2, "6.4")]:
        _report(db, rid, rdate=date(2025, 5, rid))
        _indicator(db, rid, rid, "血糖", "空腹血糖", val)
        _completed(db, rid)
    db.commit()
    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        get_change_overview(db, "123456", "张三")

    _report(db, 3, rdate=date(2026, 5, 1))
    _indicator(db, 3, 3, "血糖", "空腹血糖", "6.1")
    _completed(db, 3, level="green")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")
    assert result["cached"] is False
    assert [r["report_id"] for r in result["reports"]] == [1, 2, 3]


# ---------- LLM JSON 解析 ----------

def test_fenced_json_parsed(db):
    """带 ```json 围栏的 LLM 输出可被解析并写缓存。"""
    from app.modules.user_profile.service import get_change_overview
    from app.modules.interpretation.models import ReportInterpretation
    import json as _json

    for rid, val in [(1, "7.2"), (2, "6.4")]:
        _report(db, rid, rdate=date(2025, 5, rid))
        _indicator(db, rid, rid, "血糖", "空腹血糖", val)
        _completed(db, rid)
    _judgment(db, 1, 1, 1, "red")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model("```json\n" + _JSON_OK + "\n```")
        ok = get_change_overview(db, "123456", "张三")
    assert ok["summary"]["conclusion"] == "血糖回落"
    stored = db.query(ReportInterpretation).filter_by(report_id=2).first().comparison_summary
    assert _json.loads(stored)["payload"]["summary"]["conclusion"] == "血糖回落"


def test_invalid_json_returns_none_and_no_cache(db):
    """纯非法 JSON(无缓存)→ summary=None、仍有关键指标、不写缓存。"""
    from app.modules.user_profile.service import get_change_overview
    from app.modules.interpretation.models import ReportInterpretation

    for rid, val in [(1, "7.2"), (2, "6.4")]:
        _report(db, rid, rdate=date(2025, 5, rid))
        _indicator(db, rid, rid, "血糖", "空腹血糖", val)
        _completed(db, rid)
    _judgment(db, 1, 1, 1, "red")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model("不是JSON")
        bad = get_change_overview(db, "123456", "张三")
    assert bad["summary"] is None
    assert len(bad["key_indicators"]) == 1
    newest = db.query(ReportInterpretation).filter_by(report_id=2).first()
    assert newest.comparison_summary is None  # 未生成成功 → 不写缓存


def test_llm_failure_swallowed_and_no_cache(db):
    """LLM 抛异常被吞;summary=None;不写缓存。"""
    from app.modules.user_profile.service import get_change_overview
    from app.modules.interpretation.models import ReportInterpretation

    for rid, val in [(1, "7.2"), (2, "6.4")]:
        _report(db, rid, rdate=date(2025, 5, rid))
        _indicator(db, rid, rid, "血糖", "空腹血糖", val)
        _completed(db, rid)
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = MagicMock()
        m.return_value.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
        result = get_change_overview(db, "123456", "张三")
    assert result["summary"] is None
    assert db.query(ReportInterpretation).filter_by(report_id=2).first().comparison_summary is None


# ---------- 关键指标 ----------

def test_key_indicators_exclude_single_report_and_sort_red_first(db):
    """只出现 1 份报告的指标不进候选;red 优先于纯 |delta|≥5。"""
    from app.modules.user_profile.service import get_change_overview

    # 报告1、2 都有血糖;报告2 独有尿酸
    _report(db, 1, rdate=date(2025, 5, 1))
    _indicator(db, 1, 1, "血糖", "空腹血糖", "7.2")
    _report(db, 2, rdate=date(2026, 5, 1))
    _indicator(db, 2, 2, "血糖", "空腹血糖", "6.4")
    _indicator(db, 3, 2, "尿酸", "尿酸", "420", "μmol/L")
    _completed(db, 1, level="red", red=1)
    _completed(db, 2, level="yellow", yellow=1)
    _judgment(db, 1, 1, 1, "red")
    _judgment(db, 2, 2, 2, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    names = [k["item_name"] for k in result["key_indicators"]]
    assert names == ["空腹血糖"]  # 尿酸仅一份不入选
    ki = result["key_indicators"][0]
    assert ki["latest_value"] == "6.4"
    assert ki["latest_color"] == "yellow"
    assert ki["direction"] == "down"
    assert ki["delta_pct"] == pytest.approx(-11.11, abs=0.1)


def test_key_indicators_count_distinct_reports_not_rows(db):
    """同一报告内同一指标出现两行 → 不算「≥2 份窗口报告」候选;跨报告指标仍入选。"""
    from app.modules.user_profile.service import get_change_overview

    # 报告1 有血糖两行(6.0/6.8,同一报告重复)与血脂一行;报告2 有血糖一行(6.4)
    _report(db, 1, rdate=date(2025, 5, 1))
    _indicator(db, 1, 1, "血糖", "空腹血糖", "6.0")
    _indicator(db, 2, 1, "血糖", "空腹血糖", "6.8")
    _indicator(db, 3, 1, "血脂", "血脂", "3.1", "mmol/L")
    _report(db, 2, rdate=date(2026, 5, 1))
    _indicator(db, 4, 2, "血糖", "空腹血糖", "6.4")
    _completed(db, 1, level="red", red=1)
    _completed(db, 2, level="yellow", yellow=1)
    _judgment(db, 1, 1, 1, "red")
    _judgment(db, 2, 1, 2, "red")
    _judgment(db, 3, 1, 3, "yellow")
    _judgment(db, 4, 2, 4, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    names = [k["item_name"] for k in result["key_indicators"]]
    assert names == ["空腹血糖"]  # 血脂仅在报告1一行、且同报告重复行不算第二份 → 不入选



# ---------- worker 钩子 ----------

def test_ensure_change_overview_warm_cache_when_enough(db):
    """≥2 份 completed → 预热写缓存;不足 → 跳过不写。"""
    from app.modules.user_profile.service import ensure_change_overview
    from app.modules.interpretation.models import ReportInterpretation

    for rid, val in [(1, "7.2"), (2, "6.4")]:
        _report(db, rid, rdate=date(2025, 5, rid))
        _indicator(db, rid, rid, "血糖", "空腹血糖", val)
        _completed(db, rid)
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        ensure_change_overview(db, report_id=2)
    assert db.query(ReportInterpretation).filter_by(report_id=2).first().comparison_summary is not None

    db.add(ReportInfo(id=9, user_id="999999", name="独苗", report_date=date(2026, 5, 1)))
    _completed(db, 9, level="green")
    db.commit()
    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        ensure_change_overview(db, report_id=9)  # 不抛
    m.return_value.ainvoke.assert_not_called()
    assert db.query(ReportInterpretation).filter_by(report_id=9).first().comparison_summary is None
