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


def test_stale_after_indicator_standard_backfill_regenerates(db):
    """09-10 标准名回填(report/interpretation id 不变,仅 indicator.item_name_standard
    变化)→ 缓存指纹失效,二次必须重算并写新 fingerprint。"""
    from app.modules.user_profile.service import get_change_overview
    from app.modules.interpretation.models import ReportInterpretation
    import json as _json

    for rid, val in [(1, "300"), (2, "290")]:
        _report(db, rid, rdate=date(2025, 5, rid))
        _indicator(db, rid, rid, "血小板计数", "血小板计数（PLT）", val, "x10^9/L")
        _completed(db, rid)
    _judgment(db, 1001, 1, 1, "red")
    _judgment(db, 1002, 2, 2, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        first = get_change_overview(db, "123456", "张三")
    assert first["cached"] is False
    stored1 = _json.loads(
        db.query(ReportInterpretation).filter_by(report_id=2).first().comparison_summary)
    fp1 = stored1["fingerprint"]
    assert fp1

    for ind in db.query(ReportIndicator).filter(ReportIndicator.report_id.in_([1, 2])).all():
        ind.item_name_standard = "血小板比积（PCT）"  # 模拟 007 标准名回填,id 不变
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        second = get_change_overview(db, "123456", "张三")
    assert second["cached"] is False  # 指纹不符 → 不可复用旧 payload
    stored2 = _json.loads(
        db.query(ReportInterpretation).filter_by(report_id=2).first().comparison_summary)
    assert stored2["fingerprint"] != fp1
    assert stored2["signature"] == stored1["signature"]  # report/interp id 均未变


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

def test_key_indicators_single_report_included_and_sorted(db):
    """新口径:单份报告出现的异常指标也入选(delta_pct=None);排序按最近异常红>黄、极差降序。"""
    from app.modules.user_profile.service import get_change_overview

    # 报告1、2 都有血糖;报告2 独有尿酸(仅 1 份,新口径应入选)
    _report(db, 1, rdate=date(2025, 5, 1))
    _indicator(db, 1, 1, "血糖", "空腹血糖", "7.2")
    _report(db, 2, rdate=date(2026, 5, 1))
    _indicator(db, 2, 2, "血糖", "空腹血糖", "6.4")
    _indicator(db, 3, 2, "尿酸", "尿酸", "420", "μmol/L")
    _completed(db, 1, level="red", red=1)
    _completed(db, 2, level="yellow", yellow=1)
    _judgment(db, 1, 1, 1, "red")
    _judgment(db, 2, 2, 2, "yellow")
    _judgment(db, 3, 2, 3, "yellow")   # 尿酸(报告2独有)带黄判定,仅一份也应入选
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    names = [k["item_name"] for k in result["key_indicators"]]
    # 血糖极差 0.8 > 尿酸 0 → 血糖在前;尿酸单报告也入选
    assert names == ["空腹血糖", "尿酸"]
    by = {k["item_name"]: k for k in result["key_indicators"]}
    assert by["空腹血糖"]["latest_color"] == "yellow"
    assert by["空腹血糖"]["delta_pct"] == pytest.approx(-11.11, abs=0.1)
    assert by["尿酸"]["delta_pct"] is None


def test_key_indicators_same_report_duplicate_rows_stay_one_series(db):
    """同报告同名重复行仍是一条系列(不因行数被排除/拆分);窗口内异常即入选。"""
    from app.modules.user_profile.service import get_change_overview

    _report(db, 1, rdate=date(2025, 5, 1))
    _indicator(db, 1, 1, "血糖", "空腹血糖", "6.0")
    _indicator(db, 2, 1, "血糖", "空腹血糖", "6.8")
    _indicator(db, 3, 1, "血脂", "血脂", "3.1", "mmol/L")
    _indicator(db, 5, 1, "血脂", "血脂", "3.3", "mmol/L")
    _report(db, 2, rdate=date(2026, 5, 1))
    _indicator(db, 4, 2, "血糖", "空腹血糖", "6.4")
    _completed(db, 1, level="red", red=1)
    _completed(db, 2, level="yellow", yellow=1)
    _judgment(db, 1, 1, 1, "red")
    _judgment(db, 2, 1, 2, "red")
    _judgment(db, 3, 1, 3, "red")
    _judgment(db, 5, 1, 5, "yellow")
    _judgment(db, 4, 2, 4, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    names = [k["item_name"] for k in result["key_indicators"]]
    assert names == ["空腹血糖", "血脂"]  # 血脂(单报告双行)按新口径入选,且只一条


def test_key_indicators_exclude_child_items(db):
    """子项(血常规衍生物)不进 key_indicators;主项异常正常入选。"""
    from app.modules.user_profile.service import get_change_overview

    for rid in (1, 2):
        _report(db, rid, rdate=date(2025, rid, 1))
        _indicator(db, rid, rid, "血糖", "空腹血糖", "6.0")
        # 子项:raw 名 血小板比积,标准名已是 canonical child
        _indicator(db, rid * 10 + 1, rid, "血小板比积", "血小板比积（PCT）", "0.29", "%")
        _completed(db, rid)
    _judgment(db, 1, 1, 1, "yellow")
    _judgment(db, 2, 2, 2, "yellow")
    _judgment(db, 3, 1, 11, "yellow")   # 子项黄判定
    _judgment(db, 4, 2, 21, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    names = [k["item_name"] for k in result["key_indicators"]]
    assert names == ["空腹血糖"]  # 血小板比积（PCT）子项被过滤


def test_sort_key_standard_name_stable_across_overview_and_change(db):
    """两路排序键必须同基准:get_overview 的项带 raw item_name,change-overview 的项
    带标准名;同 severity、同极差时若按各自名字 tie-break,顺序会相反,10 项上限
    边界就会选出不同成员。"""
    from app.modules.user_profile.service import get_overview, get_change_overview

    # 原始名序:血糖(U+8840) < 谷丙转氨酶;标准名序:丙氨酸…(丙) < 空腹…(空)——恰好相反
    for rid, rdate in [(1, date(2025, 5, 1)), (2, date(2026, 5, 1))]:
        _report(db, rid, rdate=rdate)
    _indicator(db, 1, 1, "血糖", "空腹血糖（GLU）", "1.0")
    _indicator(db, 2, 1, "谷丙转氨酶", "丙氨酸氨基转移酶（ALT）", "1.0")
    _indicator(db, 3, 2, "血糖", "空腹血糖（GLU）", "2.0")
    _indicator(db, 4, 2, "谷丙转氨酶", "丙氨酸氨基转移酶（ALT）", "2.0")
    _completed(db, 1)
    _completed(db, 2)
    _judgment(db, 1, 2, 3, "yellow")   # 血糖最近点黄,极差 1.0
    _judgment(db, 2, 2, 4, "yellow")   # 谷丙最近点黄,极差 1.0
    db.commit()

    overview_names = [t["item_name_standard"]
                      for t in get_overview(db, "123456", "张三")["indicator_trends"]]
    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        change_names = [k["item_name_standard"]
                        for k in get_change_overview(db, "123456", "张三")["key_indicators"]]

    assert overview_names == change_names
    assert overview_names == ["丙氨酸氨基转移酶（ALT）", "空腹血糖（GLU）"]


def test_sort_key_standard_name_parity_guard_split(db):
    """守卫拆系列(同报告同标准名、不同原始名)下两路排序仍须同序。

    `血糖` / `葡萄糖` 都归一化到 `空腹血糖（GLU）`,同报告并存触发
    _split_item_name_collisions 拆成两条系列;两者标准名相同 → tie。
    若 change-overview 丢失 item_name_standard,会退化成按原始名 tie-break,
    与走势(按标准名)选出不同顺序/不同成员。
    """
    from app.modules.user_profile.service import get_overview, get_change_overview

    for rid, rdate in [(1, date(2025, 5, 1)), (2, date(2026, 5, 1))]:
        _report(db, rid, rdate=rdate)
    # 原始名序:血糖 < 葡萄糖;两者标准名同为 空腹血糖（GLU）→ tie。
    # 葡萄糖先于血糖插入,使守卫拆系列后 葡萄糖 在前(标准名 tie 的稳定序)。
    _indicator(db, 1, 1, "葡萄糖", "空腹血糖（GLU）", "1.0")
    _indicator(db, 2, 1, "血糖", "空腹血糖（GLU）", "1.0")
    _indicator(db, 3, 2, "葡萄糖", "空腹血糖（GLU）", "2.0")
    _indicator(db, 4, 2, "血糖", "空腹血糖（GLU）", "2.0")
    _completed(db, 1)
    _completed(db, 2)
    _judgment(db, 1, 2, 3, "yellow")   # 葡萄糖最近点黄,极差 1.0
    _judgment(db, 2, 2, 4, "yellow")   # 血糖最近点黄,极差 1.0
    db.commit()

    overview_std = [t["item_name_standard"]
                    for t in get_overview(db, "123456", "张三")["indicator_trends"]]
    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        change_std = [k["item_name_standard"]
                      for k in get_change_overview(db, "123456", "张三")["key_indicators"]]

    assert overview_std == change_std


def test_change_overview_key_indicators_capped_at_config_limit(db):
    """窗口内红/黄主项超过上限(默认10)→ key_indicators 截断为 10。"""
    from app.modules.user_profile.service import get_change_overview
    from app.config import settings

    for rid in (1, 2):
        _report(db, rid, rdate=date(2025, rid, 1))
        _completed(db, rid)
    for i in range(12):
        std = "指标%02d" % i
        _indicator(db, 100 + i, 1, std, std, "1.0", "")
        _indicator(db, 200 + i, 2, std, std, "2.0", "")
        _judgment(db, 300 + i, 2, 200 + i, "yellow")
    db.commit()

    with patch(_model_patch) as m:
        m.return_value = _fake_model(_JSON_OK)
        result = get_change_overview(db, "123456", "张三")

    assert settings.PROFILE_TREND_MAX_ITEMS == 10
    assert len(result["key_indicators"]) == 10


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


def test_series_splits_same_report_different_item_names(db):
    """旧库吞噬形态:两份报告里“血小板计数”与“血小板比积”同挂标准名。
    _series 应把不同 item_name 拆成各自系列,杜绝异量纲并线。"""
    from app.modules.user_profile.service import _series

    for rid in (1, 2):
        db.add(ReportInfo(id=rid, user_id="u1", name="甲", report_date=date(2025, rid, 1)))
        db.add(ReportIndicator(id=rid * 100 + 1, report_id=rid, item_name="血小板计数",
                               item_name_standard="血小板计数（PLT）",
                               result_value="300", unit="x10^9/L"))
        db.add(ReportIndicator(id=rid * 100 + 2, report_id=rid, item_name="血小板比积",
                               item_name_standard="血小板计数（PLT）",
                               result_value="0.29", unit="%"))
        db.add(ReportInterpretation(id=rid, report_id=rid, overall_level="green",
                                    status="completed", red_count=0, yellow_count=0, green_count=0))
    db.commit()
    rows = db.query(ReportInfo, ReportInterpretation).join(
        ReportInterpretation, ReportInterpretation.report_id == ReportInfo.id).all()
    window = list(rows)

    series = _series(db, window)
    by_name = {s["item_name"]: s for s in series}
    assert set(by_name) == {"血小板计数", "血小板比积"}
    assert {s["item_name_standard"] for s in series} == {"血小板计数（PLT）", "血小板比积（PCT）"}
    assert by_name["血小板计数"]["item_name_standard"] == "血小板计数（PLT）"
    assert by_name["血小板比积"]["item_name_standard"] == "血小板比积（PCT）"
    for s in series:
        per_report = {}
        for p in s["points"]:
            per_report.setdefault(p["report_id"], set()).add(p.get("item_name"))
        assert all(len(n) == 1 for n in per_report.values())
    assert [p["value"] for p in by_name["血小板计数"]["points"]] == ["300", "300"]
    assert [p["value"] for p in by_name["血小板比积"]["points"]] == ["0.29", "0.29"]
