# 「我的」跨报告健康变化总览(挪移报告对比 + 自动最近 N 份)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把报告详情页的「与历史报告对比」挪到患者端「我的」tab,去掉手动基线选择,自动对比最近 ≤3 份已完成 AI 解读的报告,输出总体变化总览(趋势概述/结论/建议/注意事项)+ 少量关键指标。

**Architecture:** 后端 `user_profile` 模块新增自包含 GET `/api/v1/profile/change-overview`,复用「窗口最新报告」的 `report_interpretation.comparison_summary` TEXT 列做 JSON 缓存(零 DDL);解读 worker 钩子预热缓存;旧双报告对比代码(router 两路由 + service 旧函数 + worker 旧钩子 + 前端 ComparisonCard)退役。前端删除详情页卡片,在 ProfilePage 顶部插一张新卡片。

**Tech Stack:** FastAPI + SQLAlchemy + SQLite(in-memory 测试);React 18 + antd 5 + zustand + axios;MedGo via LangChain ChatOpenAI。

## Global Constraints

- Spec:`docs/superpowers/specs/2026-09-09-profile-change-overview-design.md`(实现以 spec 为准,冲突时改 spec 先确认)。
- **零 schema 迁移**:不改任何 DDL/表/列;只复用既有 `report_interpretation.comparison_summary`(TEXT)/`comparison_baseline_id` 列。禁止动 `start.sh`、`infra/mysql/init/*`、`backend/scripts/manual_migrations/*`。
- 测试跑在 in-memory SQLite:`cd backend && .venv/bin/python -m pytest <路径> -q`。`tests/user_profile/conftest.py` 已注册 BigInteger→INTEGER 编译规则,新测试文件必须放 `tests/user_profile/` 目录内。
- 所有对 MedGo 的调用必须经 `get_chat_model()` + `asyncio.run(_guarded(...))`,LLM 失败一律吞掉记 `logger.warning`,不得冒泡进解读 worker。
- 「最近 N 份」按 `report_date` 升序取末 `settings.PROFILE_TREND_REPORT_LIMIT`(默认 3)份;`report_date IS NULL` 视为最旧(排序 key 放最前);仅 `report_interpretation.status == 'completed'` 计入。
- 双锚定过滤一律 `user_id == id_card_suffix AND name == name`。role='user' 忽略 `X-Hospital-Id`(该逻辑在 dependencies,本计划不触碰)。
- 旧功能挪移即退役:`/profile/compare`、`/profile/ai-summary` 两个路由及服务端旧双报告函数删除;`ComparisonCard.tsx` 删除。doctor-portal / statistics 不依赖 `/profile/*`,不改。
- AI 输出为纯 JSON 四键:`trend_summary/conclusion/suggestions/precautions`,中文、不下诊断、不给绝对数值、不输出 thinking/markdown 标签。
- 前端不加新依赖、不建前端单测(项目无该基建);样式沿用 `--color-*` CSS 变量与内联 style 模式。

---

### Task 1: `comparison.py` 新增 `build_change_prompt`(纯函数)

**Files:**
- Modify: `backend/app/modules/user_profile/comparison.py`(追加函数)
- Test: `backend/tests/user_profile/test_comparison.py`(追加用例)

**Interfaces:**
- Consumes: 无(纯函数)。
- Produces: `build_change_prompt(reports: list[dict], key_indicators: list[dict]) -> str`。`reports` 每项含 `report_date/overall_level/red_count/yellow_count/green_count`(升序);`key_indicators` 每项含 `item_name/unit/delta_pct/points`(`points[].report_date/value/color`)。

- [ ] **Step 1: 追加失败用例**

在 `backend/tests/user_profile/test_comparison.py` 末尾追加:

```python
def test_build_change_prompt_contains_window_and_sections():
    from app.modules.user_profile.comparison import build_change_prompt

    reports = [
        {"report_date": "2024-05-01", "overall_level": "green",
         "red_count": 0, "yellow_count": 1, "green_count": 10},
        {"report_date": "2025-05-01", "overall_level": "yellow",
         "red_count": 1, "yellow_count": 2, "green_count": 9},
    ]
    key_indicators = [
        {"item_name": "空腹血糖", "unit": "mmol/L", "delta_pct": -11.1,
         "points": [
             {"report_date": "2024-05-01", "value": "7.2", "color": "red"},
             {"report_date": "2025-05-01", "value": "6.4", "color": "green"},
         ]},
    ]
    prompt = build_change_prompt(reports, key_indicators)
    assert "2024-05-01" in prompt and "2025-05-01" in prompt
    assert "空腹血糖" in prompt
    assert "trend_summary" in prompt and "precautions" in prompt
    assert "conclusion" in prompt and "suggestions" in prompt
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_comparison.py::test_build_change_prompt_contains_window_and_sections -q`
Expected: `FAILED ... ImportError: cannot import name 'build_change_prompt'`

- [ ] **Step 3: 实现**

在 `backend/app/modules/user_profile/comparison.py` 文件末尾追加:

```python
def build_change_prompt(reports: list[dict], key_indicators: list[dict]) -> str:
    """拼出给 MedGo 的跨最近 N 份报告总体变化总览 prompt(纯 JSON 四键输出)。

    reports: 升序的报告头(含 overall_level / 红黄绿计数)。
    key_indicators: 排序后的关键指标(含 delta_pct / points)。
    """
    if not reports:
        return ""
    report_lines = [
        "- {date}:总体{level},红区{r} 黄区{y} 绿区{g}".format(
            date=r.get("report_date") or "未知",
            level=r.get("overall_level") or "未知",
            r=r.get("red_count", 0),
            y=r.get("yellow_count", 0),
            g=r.get("green_count", 0),
        )
        for r in reports
    ]
    ind_lines = []
    for ind in key_indicators[:8]:
        name = ind.get("item_name") or "?"
        unit = ind.get("unit") or ""
        segs = []
        for p in ind.get("points", []):
            d = (p.get("report_date") or "?").__str__()[:7]
            c = p.get("color") or ""
            segs.append("{d} {v}{suffix}".format(
                d=d, v=p.get("value", ""),
                suffix=("(" + c + ")") if c else ""))
        d = ind.get("delta_pct")
        delta_txt = ""
        if d is not None:
            delta_txt = ",{arrow}{absv}%".format(
                arrow="↑" if d > 0 else "↓", absv=abs(round(float(d), 1)))
        ind_lines.append("- {name}{unit}:{segs}{delta}".format(
            name=name,
            unit=("(" + unit + ")") if unit else "",
            segs=" → ".join(segs) if segs else "无连续数值",
            delta=delta_txt))
    ind_text = "\n".join(ind_lines) or "  (窗口内无连续可量化的关键指标)"

    return f"""你是体检报告解读助手。下面是该用户最近 {len(reports)} 次体检报告的窗口数据,请给出一份总体性健康变化总览,用通俗中文。

## 各次报告(按日期升序)
{chr(10).join(report_lines)}

## 关键指标走势(按时间先后列出每次值;red=红区异常,yellow=黄区偏高,green=绿区)
{ind_text}

## 输出要求
只输出一个 JSON 对象(不要 markdown 代码块、不要 thinking 标签),键严格为以下四个:
- "trend_summary": 一段(≤80字)总体变化概述,概括红/黄区数量增减与整体走向
- "conclusion": (≤120字)提炼窗口内最重要的指标变化结论,落到上面列出的具体指标
- "suggestions": (≤150字)针对可量化的变化指标(如血糖、血脂)给 1-3 条健康建议
- "precautions": (≤100字)复查与就医注意事项,异常时提示尽快就医
要求:不下诊断;不要编造上面未出现的指标或数值;不提绝对数值;内容仅供健康参考。
"""
```

- [ ] **Step 4: 运行验证通过**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_comparison.py -q`
Expected: 全绿(新增 1 + 既有 compute_delta/trend_direction 用例)。

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/user_profile/comparison.py backend/tests/user_profile/test_comparison.py
git commit -m "feat(profile): comparison.py 新增 build_change_prompt 跨报告变化总览 prompt"
```

---

### Task 2: service.py 新增 change-overview 核心逻辑(窗口/关键指标/缓存/LLM)

**Files:**
- Modify: `backend/app/modules/user_profile/service.py`(import + 文件末尾追加新函数)
- Test: `backend/tests/user_profile/test_change_overview.py`(新建)

**Interfaces:**
- Consumes:
  - `settings.PROFILE_TREND_REPORT_LIMIT`(`backend/app/config.py`,默认 3)。
  - `compute_delta / trend_direction / _try_float / build_change_prompt`(Task 1 产物;`_try_float` 为 comparison.py 私有,同包可 import)。
  - `get_chat_model / _guarded`(`app.ai.llm`)、`strip_think_tags`(`app.ai.agents.think_filter`)。
- Produces(后续 Task 3/4/5 依赖):
  - `empty_change_overview(covered: int) -> dict`
  - `get_change_overview(db: Session, user_id: str, name: str) -> dict`
  - `ensure_change_overview(db: Session, report_id: int) -> None`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/user_profile/test_change_overview.py`:

```python
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
```

- [ ] **Step 2: 运行验证失败**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_change_overview.py -q`
Expected: `FAILED`(多处 `ImportError: cannot import name 'get_change_overview' from 'app.modules.user_profile.service'` 之类)。

- [ ] **Step 3: 实现(改 import + 追加函数)**

Step 3a — 修改 `backend/app/modules/user_profile/service.py` 头部 import(第 1-16 行替换为):

```python
import asyncio
import json
import logging
import re
from datetime import date, datetime
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.report.models import ReportInfo, ReportIndicator
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
from app.modules.user_profile.comparison import (
    match_indicators, compute_delta, judge_status, trend_direction,
    build_comparison_prompt, build_change_prompt, _try_float,
)
from app.ai.llm import get_chat_model, _guarded
from app.ai.agents.think_filter import strip_think_tags
from app.config import settings

logger = logging.getLogger(__name__)
```

Step 3b — 在 `backend/app/modules/user_profile/service.py` **文件末尾**追加:

```python
# ===========================================================================
# 跨报告健康变化总览(2026-09-09):自动对比最近 N 份已完成解读的报告
# ===========================================================================

def empty_change_overview(covered: int) -> dict:
    """不足 2 份可对比报告时的降级响应。covered = 该锚定已解读报告数。"""
    return {
        "reports": [],
        "covered": covered,
        "reason": "insufficient",
        "key_indicators": [],
        "summary": None,
        "cached": False,
    }


def _change_window(db: Session, user_id: str, name: str) -> list:
    """返回 [(ReportInfo, ReportInterpretation), ...] 升序,仅 completed,取最近 N 份。

    report_date 升序,None 视为最旧放最前(与 /overview 口径一致);取末
    PROFILE_TREND_REPORT_LIMIT 份。
    """
    rows = (
        db.query(ReportInfo, ReportInterpretation)
        .join(ReportInterpretation, ReportInterpretation.report_id == ReportInfo.id)
        .filter(
            ReportInfo.user_id == user_id,
            ReportInfo.name == name,
            ReportInterpretation.status == "completed",
        )
        .all()
    )
    window = []
    for report, interp in rows:
        window.append((report, interp))

    def _key(pair):
        report = pair[0]
        return (report.report_date is not None, report.report_date or date.min, report.id)

    ordered = sorted(window, key=_key)
    return ordered[-settings.PROFILE_TREND_REPORT_LIMIT:]


def _report_header(pair) -> dict:
    report, interp = pair
    return {
        "report_id": report.id,
        "report_date": report.report_date.isoformat() if report.report_date else None,
        "overall_level": interp.overall_level,
        "red_count": interp.red_count,
        "yellow_count": interp.yellow_count,
        "green_count": interp.green_count,
    }


def _read_cached_overview(interp: Optional[ReportInterpretation], window: list) -> Optional[dict]:
    """列内容为 JSON 且 signature 与当前窗口一致 → 返回 payload;否则 None(含旧纯文本)。"""
    if not interp or not interp.comparison_summary:
        return None
    try:
        data = json.loads(interp.comparison_summary)
    except (TypeError, ValueError):
        return None
    sig = [{"report_id": pair[0].id, "interp_id": pair[1].id} for pair in window]
    if data.get("signature") != sig:
        return None
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def _series(db: Session, window: list) -> list[dict]:
    """按 item_name_standard 聚合窗口内数值指标为 points;value 保留原始字符串。"""
    report_ids = [pair[0].id for pair in window]
    rid2date = {pair[0].id: _report_header(pair)["report_date"] for pair in window}
    inds = db.query(ReportIndicator).filter(ReportIndicator.report_id.in_(report_ids)).all()
    colors: dict = {}
    if inds:
        judgments = (
            db.query(IndicatorJudgment)
            .join(ReportInterpretation,
                  IndicatorJudgment.interpretation_id == ReportInterpretation.id)
            .filter(ReportInterpretation.report_id.in_(report_ids))
            .all()
        )
        colors = {j.indicator_id: j.color_level for j in judgments}

    by_key: dict = {}
    for ind in inds:
        if _try_float(ind.result_value) is None:
            continue
        key = ind.item_name_standard or ind.item_name
        if not key:
            continue
        item = by_key.setdefault(key, {
            "item_name": key,
            "item_name_standard": ind.item_name_standard,
            "unit": ind.unit,
            "points": [],
        })
        item["points"].append({
            "report_id": ind.report_id,
            "report_date": rid2date.get(ind.report_id),
            "value": str(ind.result_value).strip(),
            "color": colors.get(ind.id),
        })
    for item in by_key.values():
        item["points"].sort(key=lambda p: (p["report_date"] is not None, p["report_date"] or ""))
    return list(by_key.values())


def _severity(points: list[dict]) -> int:
    """窗口内最近一次红/黄(红=0,黄=1),否则 2。"""
    for p in reversed(points):
        if p.get("color") in ("red", "yellow"):
            return 0 if p["color"] == "red" else 1
    return 2


def _endpoint_pct(points: list[dict]) -> Optional[float]:
    """最新点相对最旧点的 delta_pct。"""
    if len(points) < 2:
        return None
    pair = compute_delta(points[-1]["value"], points[0]["value"])
    return pair[1] if pair else None


def _rank_key_indicators(db: Session, window: list) -> list[dict]:
    """关键指标:出现在 ≥2 份窗口报告、且窗口内有过红/黄或首尾 |delta_pct|≥5。

    排序:最近异常点红 > 黄 > 无,同级按 |delta_pct| 降序,再按指标名。
    """
    ranked = []
    for item in _series(db, window):
        points = item["points"]
        if len(points) < 2:
            continue
        pct = _endpoint_pct(points)
        sev = _severity(points)
        if sev >= 2 and (pct is None or abs(pct) < 5):
            continue
        ranked.append({
            "item_name": item["item_name"],
            "unit": item["unit"],
            "latest_value": points[-1]["value"],
            "latest_color": points[-1]["color"],
            "direction": trend_direction(points),
            "delta_pct": pct,
            "points": points,
            "_sev": sev,
            "_pct_abs": abs(pct) if pct is not None else 0.0,
        })
    ranked.sort(key=lambda x: (x["_sev"], -x["_pct_abs"], x["item_name"]))
    for x in ranked:
        x.pop("_sev", None)
        x.pop("_pct_abs", None)
    return ranked


def _parse_change_json(content: str) -> Optional[dict]:
    """宽容解析 MedGo 输出的 JSON 四键对象;失败返回 None。"""
    if not content:
        return None
    text0 = content.strip()
    if text0.startswith("```"):
        text0 = re.sub(r"^```[A-Za-z]*\n?", "", text0)
        text0 = re.sub(r"```$", "", text0).strip()
    try:
        obj = json.loads(text0)
    except (TypeError, ValueError):
        m = re.search(r"\{.*\}", text0, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except (TypeError, ValueError):
            return None
    if not isinstance(obj, dict):
        return None
    keys = ("trend_summary", "conclusion", "suggestions", "precautions")
    if not all(isinstance(obj.get(k), str) for k in keys):
        return None
    return {k: obj.get(k, "").strip() for k in keys}


def _call_llm_for_change_overview(prompt: str) -> Optional[dict]:
    """调 MedGo 生成总览。失败/解析失败返回 None 并记 warning。"""
    try:
        model = get_chat_model(streaming=False)
        resp = asyncio.run(_guarded(model.ainvoke([("user", prompt)], max_tokens=1024)))
        return _parse_change_json(strip_think_tags(resp.content or ""))
    except Exception as e:
        logger.warning("change overview LLM call failed: %s", e)
        return None


def get_change_overview(db: Session, user_id: str, name: str) -> dict:
    """GET /profile/change-overview 主入口。不足 2 份降级;否则读缓存或生成并写回。"""
    window = _change_window(db, user_id, name)
    if len(window) < 2:
        return empty_change_overview(len(window))
    newest_interp = window[-1][1]
    cached = _read_cached_overview(newest_interp, window)
    if cached:
        cached["cached"] = True
        return cached

    key_indicators = _rank_key_indicators(db, window)
    reports = [_report_header(pair) for pair in window]
    payload = {
        "reports": reports,
        "covered": len(reports),
        "key_indicators": key_indicators[:5],
        "summary": None,
        "cached": False,
    }
    prompt = build_change_prompt(reports, key_indicators)
    summary = _call_llm_for_change_overview(prompt)
    payload["summary"] = summary
    if summary:
        sig = [{"report_id": pair[0].id, "interp_id": pair[1].id} for pair in window]
        newest_interp.comparison_summary = json.dumps(
            {"signature": sig, "payload": payload}, ensure_ascii=False)
        newest_interp.comparison_baseline_id = None
        db.commit()
    return payload


def ensure_change_overview(db: Session, report_id: int) -> None:
    """worker 钩子:解读完成后按锚定重算窗口并预热缓存。任何异常吞掉不冒泡。"""
    try:
        report = db.query(ReportInfo).filter_by(id=report_id).first()
        if not report:
            return
        get_change_overview(db, report.user_id, report.name)
    except Exception as e:
        logger.warning("change overview pre-generation failed: %s", e)
```

- [ ] **Step 4: 运行新测试验证通过**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile/test_change_overview.py -q`
Expected: 全绿。随后跑既有模块测试确认无回归:Run `cd backend && .venv/bin/python -m pytest tests/user_profile -q` Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/user_profile/service.py backend/tests/user_profile/test_change_overview.py
git commit -m "feat(profile): service 新增 change-overview 取窗/关键指标/缓存/LLM 生成"
```

---

### Task 3: Router 暴露 `/change-overview`,退役 `/compare` 与 `/ai-summary`

**Files:**
- Modify: `backend/app/modules/user_profile/router.py`

**Interfaces:**
- Consumes: `service.get_change_overview`、`service.empty_change_overview`(Task 2 产物)。
- Produces: `GET /api/v1/profile/change-overview`(auth/锚定与模块其它路由一致)。

- [ ] **Step 1: 改路由**

把 `backend/app/modules/user_profile/router.py` 第 35-59 行(`/compare` + `/ai-summary` 两个 handler)整体替换为:

```python
@router.get("/change-overview")
def change_overview(
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    uid, nm = user_identity(current_user)
    if uid is None:
        return service.empty_change_overview(0)
    return service.get_change_overview(db, uid, nm)
```

- [ ] **Step 2: 冒烟验证**

Run: `cd backend && .venv/bin/python -c "from app.modules.user_profile import router; assert any(getattr(r,'path','')=='/change-overview' for r in router.router.routes); assert not any(getattr(r,'path','') in ('/compare','/ai-summary') for r in router.router.routes); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/modules/user_profile/router.py
git commit -m "feat(profile): router 暴露 GET /profile/change-overview,退役 compare/ai-summary"
```

---

### Task 4: interpretation worker 钩子换成 `ensure_change_overview`

**Files:**
- Modify: `backend/app/modules/interpretation/worker.py:62-72`
- Modify: `backend/tests/followup/test_worker_hook.py:45`
- Modify: `backend/tests/test_interp_worker_bulk.py:54`(及该文件内 `cmp_mock` 命名注释)

**Interfaces:**
- Consumes: `app.modules.user_profile.service.ensure_change_overview`(Task 2)。

- [ ] **Step 1: 改 worker.py**

将 `backend/app/modules/interpretation/worker.py` 第 63-72 行替换为:

```python
            # register change overview cache(failures don't affect interp completion)
            try:
                from app.modules.user_profile.service import (
                    ensure_change_overview,
                )
                ensure_change_overview(db, report_id)
            except Exception as e:
                print(
                    f"Change overview generation failed for report {report_id}: {e}",
                    flush=True,
                )
```

- [ ] **Step 2: 更新 patch 目标**

`backend/tests/followup/test_worker_hook.py:45`:
`patch("app.modules.user_profile.service.try_generate_comparison_summary")` → `patch("app.modules.user_profile.service.ensure_change_overview")`

`backend/tests/test_interp_worker_bulk.py:54` 同样改为 `patch("app.modules.user_profile.service.ensure_change_overview")`。

- [ ] **Step 3: 运行验证**

Run: `cd backend && .venv/bin/python -m pytest tests/followup/test_worker_hook.py tests/test_interp_worker_bulk.py -q`
Expected: 全绿(worker 仍调用真实 ensure_change_overview,patch 只防止真实预热;bulk 用例 `test_comparison_summary_failure_doesnt_break` 仍验证侧载失败不影响进度)。

- [ ] **Step 4: Commit**

```bash
git add backend/app/modules/interpretation/worker.py backend/tests/followup/test_worker_hook.py backend/tests/test_interp_worker_bulk.py
git commit -m "refactor(interp): worker 钩子换用 ensure_change_overview 预热变化总览缓存"
```

---

### Task 5: 删除退役的旧双报告后端代码与测试

**Files:**
- Modify: `backend/app/modules/user_profile/service.py`
- Modify: `backend/app/modules/user_profile/comparison.py`
- Modify: `backend/tests/user_profile/test_service.py`(删除引用已删函数的用例)
- Modify: `backend/tests/user_profile/test_comparison.py`(删除 match_indicators / judge_status / build_comparison_prompt 用例)

**Interfaces:**
- 本 Task 不新增对外接口;目标是清理。

- [ ] **Step 1: service.py 头部 import 收敛**

`backend/app/modules/user_profile/service.py` 头部 import 中,`from app.modules.user_profile.comparison import (...)` 替换为:

```python
from app.modules.user_profile.comparison import (
    compute_delta, trend_direction, _try_float, build_change_prompt,
)
```

- [ ] **Step 2: 删除 service.py 旧函数与常量**

删除(整段,含 `_build_indicator_diff`、`_indicator_to_dict`、`_filter_abnormal_top`、`get_comparison`、`get_ai_summary`、`_call_llm_for_summary`、`try_generate_comparison_summary`,以及文件顶部第 20-23 行四个常量 `MAX_HISTORY_REPORTS / TOP_INDICATORS_DEFAULT / TOP_ABNORMAL_FOR_PROMPT / STATUS_STABLE_PCT`)。保留:`_auto_select_baseline`、`get_overview`、Task 2 新增全部函数。修改后文件从 `_build_indicator_diff` 原起始处起,**只能**是 Task 2 追加的「跨报告健康变化总览」区块。

- [ ] **Step 3: 删除 comparison.py 旧函数**

删除 `backend/app/modules/user_profile/comparison.py` 中 `match_indicators`、`judge_status`、`build_comparison_prompt`;保留 `_try_float`、`compute_delta`、`trend_direction`、`build_change_prompt`(Task 1)。

- [ ] **Step 4: 删除旧测试用例**

`backend/tests/user_profile/test_service.py` 中删除以下用例(行号基于改动前,请按函数名删):
`test_try_generate_comparison_summary_writes_cache_on_first_call`、`test_try_generate_comparison_summary_skips_when_no_history_report`、`test_try_generate_comparison_summary_swallows_llm_failure`、`test_try_generate_comparison_summary_skips_when_cache_hit`、`test_get_ai_summary_cache_hit_returns_cached_true`、`test_get_ai_summary_calls_llm_when_baseline_mismatch`、`test_get_comparison_does_not_double_count_baseline_with_diff_raw_names`、`test_get_comparison_raises_not_found_when_report_missing`、`test_get_comparison_raises_validation_when_baseline_not_owned`、`test_get_ai_summary_raises_validation_when_baseline_not_owned`、`test_get_comparison_earliest_report_returns_baseline_and_diff`。文件顶部 docstring 若仍提 `try_generate_comparison_summary`,改成一句话概述即可。

`backend/tests/user_profile/test_comparison.py` 中删除 `match_indicators` 三个用例、`judge_status` 一个用例、`build_comparison_prompt` 一个用例,并把顶部 import 收敛为 `compute_delta, trend_direction, build_change_prompt`(`test_service.py` 相应保留 `_auto_select_baseline` 与 overview 用例不动)。

- [ ] **Step 5: 跑全量相关测试**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile tests/followup/test_worker_hook.py tests/test_interp_worker_bulk.py -q`
Expected: 全绿。

- [ ] **Step 6: 静态冒烟(确认无残留引用)**

Run: `cd backend && .venv/bin/python -c "import ast,glob; [ast.parse(open(f).read()) for f in glob.glob('app/**/*.py',recursive=True)]; from app.modules.user_profile import service, comparison, router; print('ok')"`
Expected: `ok`(无 ImportError / 语法错)。

- [ ] **Step 7: Commit**

```bash
git add backend/app/modules/user_profile/service.py backend/app/modules/user_profile/comparison.py backend/tests/user_profile/test_service.py backend/tests/user_profile/test_comparison.py
git commit -m "refactor(profile): 退役双报告对比旧函数与旧测试(compare/ai-summary)"
```

---

### Task 6: 前端 —— 报告详情页移除 ComparisonCard

**Files:**
- Modify: `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`
- Delete: `frontend/packages/user-portal/src/components/ComparisonCard.tsx`

**Interfaces:**
- 无新接口;删除组件。

- [ ] **Step 1: 删 import**

`frontend/packages/user-portal/src/pages/ReportDetailPage.tsx` 第 11 行 `import ComparisonCard from '../components/ComparisonCard';` 删除。

- [ ] **Step 2: 删渲染块**

第 272-274 行:

```tsx
      {interpretation?.status === 'completed' && (
        <ComparisonCard reportId={Number(id)} />
      )}

```

整体删除(含其后空行),保留 `<InterpretationReportCard ... />`。

- [ ] **Step 3: 删除文件**

Run: `rm frontend/packages/user-portal/src/components/ComparisonCard.tsx`

- [ ] **Step 4: 编译冒烟**

Run(若装有 node_modules):`cd frontend/packages/user-portal && npx tsc --noEmit`
Expected: 无错(无 node_modules 则跳过,留待 Task 7 手工验证)。

- [ ] **Step 5: Commit**

```bash
git add -A frontend/packages/user-portal/src/pages/ReportDetailPage.tsx frontend/packages/user-portal/src/components/ComparisonCard.tsx
git commit -m "feat(user-portal): 报告详情页移除与历史报告对比卡片"
```

---

### Task 7: 前端 —— 「我的」页新增近期健康变化卡片

**Files:**
- Create: `frontend/packages/user-portal/src/components/ChangeOverviewCard.tsx`
- Modify: `frontend/packages/user-portal/src/pages/ProfilePage.tsx`

**Interfaces:**
- Consumes: `GET /api/v1/profile/change-overview`(Task 3),结构同 spec §响应。
- Produces: `<ChangeOverviewCard />`(无 props,自取数)。

- [ ] **Step 1: 新建组件**

新建 `frontend/packages/user-portal/src/components/ChangeOverviewCard.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Spin } from 'antd';
import { useUserStore } from '../stores/userStore';
import ColorBadge from './ColorBadge';

interface ReportHead {
  report_id: number; report_date: string | null; overall_level: string | null;
  red_count: number; yellow_count: number; green_count: number;
}
interface IndicatorPoint {
  report_id: number; report_date: string | null; value: string; color: string | null;
}
interface KeyIndicator {
  item_name: string; unit: string | null; latest_value: string; latest_color: string | null;
  direction: string | null; delta_pct: number | null; points: IndicatorPoint[];
}
interface ChangeSummary {
  trend_summary: string; conclusion: string; suggestions: string; precautions: string;
}
interface ChangeOverview {
  reports: ReportHead[]; covered: number; reason?: string;
  key_indicators: KeyIndicator[]; summary: ChangeSummary | null; cached: boolean;
}

const CARD = {
  background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
  padding: '16px 20px', boxShadow: 'var(--shadow-sm)',
  border: '1px solid var(--color-border-light)', marginBottom: 16,
};

function AiBlock({ label, text, accent }: { label: string; text: string; accent?: boolean }) {
  return (
    <div style={{
      marginBottom: 10, padding: '8px 12px', borderRadius: 'var(--radius-sm)',
      background: accent ? 'var(--color-primary-light)' : 'var(--color-bg)',
      borderLeft: `3px solid ${accent ? 'var(--color-red)' : 'var(--color-primary)'}`,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--color-text)', whiteSpace: 'pre-wrap' }}>{text}</div>
    </div>
  );
}

export default function ChangeOverviewCard() {
  const { api } = useUserStore();
  const [data, setData] = useState<ChangeOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    api.get('/profile/change-overview')
      .then(r => setData(r.data))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div style={CARD}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>📈 近期健康变化</div>
        <div style={{ textAlign: 'center', padding: 16 }}><Spin size="small" /> 正在生成跨报告分析...</div>
      </div>
    );
  }
  if (!data || data.reason === 'insufficient' || data.covered < 2) {
    return (
      <div style={CARD}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>📈 近期健康变化</div>
        <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>
          完成 ≥2 份报告的 AI 解读后,这里将自动对比最近 {data?.covered ?? 0} 份报告并生成健康变化总览。
        </div>
      </div>
    );
  }

  const firstDate = data.reports[0]?.report_date;
  const lastDate = data.reports[data.reports.length - 1]?.report_date;
  const range = [firstDate, lastDate].filter(Boolean).join(' ~ ');
  const shown = showAll ? data.key_indicators : data.key_indicators.slice(0, 5);
  const s = data.summary;

  return (
    <div style={CARD}>
      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>📈 近期健康变化</div>
      <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 12 }}>
        已自动对比最近 {data.covered} 份已完成解读的报告{range ? `(${range})` : ''},无需手动选择
      </div>

      {s ? (
        <>
          <AiBlock label="总体变化" text={s.trend_summary} accent />
          <AiBlock label="结论" text={s.conclusion} />
          <AiBlock label="建议" text={s.suggestions} />
          <AiBlock label="注意事项" text={s.precautions} />
        </>
      ) : (
        <div style={{ fontSize: 13, color: 'var(--color-text-secondary)', padding: '4px 0 12px' }}>
          AI 总览暂不可用,请查看下方关键指标变化。
        </div>
      )}

      {data.key_indicators.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 4 }}>
            关键指标变化
          </div>
          {shown.map((k, i) => {
            const dir = k.direction === 'down' ? '↓' : (k.direction === 'up' ? '↑' : '');
            const pct = k.delta_pct != null ? `${Math.abs(Math.round(k.delta_pct * 10) / 10)}%` : '';
            return (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 0', borderBottom: '1px solid var(--color-border-light)', fontSize: 13,
              }}>
                <span style={{ flex: 1, fontWeight: 500, minWidth: 0 }}>{k.item_name}</span>
                <span style={{ color: 'var(--color-text-secondary)', whiteSpace: 'nowrap' }}>
                  {k.latest_value}{k.unit ? ` ${k.unit}` : ''}
                  {k.latest_color && <ColorBadge level={k.latest_color} size="sm" />}
                </span>
                <span style={{
                  whiteSpace: 'nowrap', fontSize: 12, fontWeight: 600,
                  color: dir === '↑' ? 'var(--color-red)' : 'var(--color-green)',
                }}>
                  {dir}{pct}
                </span>
              </div>
            );
          })}
          {data.key_indicators.length > 5 && (
            <button onClick={() => setShowAll(!showAll)} style={{
              border: 'none', background: 'none', color: 'var(--color-primary)',
              fontSize: 12, cursor: 'pointer', padding: '8px 0',
            }}>
              {showAll ? '收起' : `展开全部 (${data.key_indicators.length})`}
            </button>
          )}
        </div>
      )}

      <div style={{ marginTop: 12, fontSize: 11, color: 'var(--color-text-secondary)', lineHeight: 1.6 }}>
        本内容由 AI 依据指标数值自动生成,仅供参考,不构成医疗诊断;指标异常请遵医嘱复查。
      </div>
    </div>
  );
}
```

- [ ] **Step 2: ProfilePage 插入组件**

`frontend/packages/user-portal/src/pages/ProfilePage.tsx`:

Step 2a — 第 8 行后加 import:
`import ChangeOverviewCard from '../components/ChangeOverviewCard';`

Step 2b — 在第一个汇总卡片的闭合 `</div>`(现第 109 行)之后、「指标走势」卡片之前,插入:

```tsx
      <ChangeOverviewCard />

```

- [ ] **Step 3: 编译冒烟**

Run(若装有 node_modules):`cd frontend/packages/user-portal && npx tsc --noEmit`
Expected: 无错。无 node_modules 则跳过,留待手工验证。

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/user-portal/src/components/ChangeOverviewCard.tsx frontend/packages/user-portal/src/pages/ProfilePage.tsx
git commit -m "feat(user-portal): 「我的」页新增近期健康变化自动对比卡片"
```

---

### Task 8: 后端全量回归 + spec 备注落库

**Files:**
- Modify: 无(仅验证;可选 AGENTS.md 备注)

- [ ] **Step 1: 全量相关后端测试**

Run: `cd backend && .venv/bin/python -m pytest tests/user_profile tests/followup tests/test_interp_worker_bulk.py -q`
Expected: 全绿。

- [ ] **Step 2: 前端编译(如可)**

Run: `cd frontend/packages/user-portal && npx tsc --noEmit`(无 node_modules 则说明并跳过)。

- [ ] **Step 3: 人工验收要点(交付时转述给用户)**

1. 报告详情页不再有「与历史报告对比」卡片,AI 解读报告卡仍在。
2. 「我的」页汇总卡下方出现「📈 近期健康变化」:≥2 份已完成解读时显示自动对比说明 + 三/四节 AI 总览 + 关键指标行;首次冷缓存出现加载态,二次秒开;报告不足显示引导文案。
3. 用 app-login token `curl` `GET /api/v1/profile/change-overview` 返回同一自包含结构。

- [ ] **Step 4: AGENTS.md 备注(可选但推荐)**

若认为值得记录,在 AGENTS.md 追加几行(退役 `/profile/compare`、`/ai-summary` 与 `try_generate_comparison_summary`;`comparison_summary` 列语义改为「跨报告健康变化总览 JSON」;新 GET `/profile/change-overview`)。不强制,不阻塞本计划验收。

- [ ] **Step 5: Commit(如有改动)**

```bash
git add AGENTS.md
git commit -m "docs(AGENTS): 记录 change-overview 取代报告对比 + 列语义变更" # 仅当 Step 4 执行
```
