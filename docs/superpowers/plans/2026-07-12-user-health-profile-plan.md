# 用户健康档案 + 上传对比功能 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 user-portal 把 `/profile` 路由从设置菜单重写为「我的健康档案」页(展示历史报告聚合趋势),并在 `ReportDetailPage` 嵌入 `ComparisonCard`(展示当前报告与上一份报告的指标差异 + AI 小结)。

**Architecture:** 后端新增 `app/modules/user_profile/` 模块(三个接口 + worker 钩子),`report_interpretation` 表加两列缓存 AI 小结;前端重写 `ProfilePage.tsx`,新建 `ComparisonCard.tsx` / `IndicatorTrendChart.tsx`。跨报告指标按 `item_name_standard` 匹配(空值 fallback 到 `item_name`)。

**Tech Stack:** Python 3.10 / FastAPI / SQLAlchemy 2.x / MySQL 8 / LangChain 1.x(ChatOpenAI);React 18 + TypeScript / Ant Design 5 / Vite 6。

## Global Constraints

- 后端依赖通过 `backend/.venv` 跑(`uv run pytest` / `.venv/bin/python`),**不要走 `.venv-vllm-cu12`**(那个仅供 vllm 服务,见 `AGENTS.md`)。
- Python 业务代码**不写注释**(遵循仓库规约);测试代码可写 docstring。
- 测试命令:后端 `cd backend && uv run pytest <path> -v`;前端 `npm run build -w @hospital/user-portal` 验类型与构建。
- DDL 在 hospital_H001 与 hospital_template 两个库都要执行(同 `2026-07-06-interpretation-report-plan.md` Task 1 的惯例)。
- AI 小结的 LLM 调用走 `get_chat_model(streaming=False)`,MedGo 模型,`strip_think_tags` 清洗,`max_tokens=512`。
- 不动 `TrendMiniChart.tsx`(服务 statistics 模块,新组件 `IndicatorTrendChart.tsx`)。
- 跨模块 import 走 lazy(在函数内 import)避免循环依赖。
- `ReportInterpretation.comparison_summary` 与 `comparison_baseline_id` 两列 nullable,存量行 NULL,不破坏现状。
- 前端无单测,以 `npm run build` 通过为准;手动验证对比卡与趋势图布局。

---

## File Structure

### 后端新增

| 路径 | 责任 |
|------|------|
| `backend/scripts/manual_migrations/002_add_comparison_summary.sql` | DDL 迁移脚本 |
| `backend/app/modules/user_profile/__init__.py` | 模块包 |
| `backend/app/modules/user_profile/comparison.py` | 纯函数:指标匹配、delta 计算、status 判定、prompt 构造 |
| `backend/app/modules/user_profile/service.py` | DB 查询、overview/compare/ai_summary 业务逻辑、worker 钩子 `try_generate_comparison_summary` |
| `backend/app/modules/user_profile/router.py` | 3 个接口:GET /profile/overview、/profile/compare、/profile/ai-summary |
| `backend/tests/user_profile/__init__.py` | 测试包 |
| `backend/tests/user_profile/test_comparison.py` | 纯函数单测 |
| `backend/tests/user_profile/test_service.py` | worker 钩子集成测试(含 mock LLM) |

### 后端改造

| 路径 | 改动 |
|------|------|
| `backend/app/modules/interpretation/models.py` | `ReportInterpretation` 加 2 字段 |
| `backend/app/modules/interpretation/worker.py` | `handle_interpretation_task` 解读完成后调 `try_generate_comparison_summary` |
| `backend/app/main.py` | 注册 `user_profile_router` |

### 前端新增

| 路径 | 责任 |
|------|------|
| `frontend/packages/user-portal/src/components/ComparisonCard.tsx` | 报告对比卡(基准切换 + 指标差异表 + AI 小结) |
| `frontend/packages/user-portal/src/components/IndicatorTrendChart.tsx` | 数值型指标走势 SVG 折线图 |

### 前端改造

| 路径 | 改动 |
|------|------|
| `frontend/packages/user-portal/src/pages/ProfilePage.tsx` | 重写为健康档案页 |
| `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx` | 解读完成后挂载 `ComparisonCard` |

---

### Task 1: 数据库迁移 — 给 `report_interpretation` 加两列

**Files:**
- Create: `backend/scripts/manual_migrations/002_add_comparison_summary.sql`
- Modify: `backend/app/modules/interpretation/models.py:14-19`

**Interfaces:**
- Produces: `report_interpretation.comparison_summary TEXT NULL`、`comparison_baseline_id BIGINT NULL` 两列;ORM 上 `ReportInterpretation` 模型对应属性。

- [ ] **Step 1: 写迁移 SQL 文件**

文件路径:`backend/scripts/manual_migrations/002_add_comparison_summary.sql`

```sql
-- Add columns to cache AI-generated comparison summary between reports
ALTER TABLE report_interpretation
  ADD COLUMN comparison_summary TEXT NULL AFTER summary_refs,
  ADD COLUMN comparison_baseline_id BIGINT NULL AFTER comparison_summary;
```

- [ ] **Step 2: 在 hospital_H001 库执行迁移**

```bash
mkdir -p /data/project/hospitalKnowledgeBase/backend/scripts/manual_migrations && \
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
from app.core.database import get_hospital_db
from sqlalchemy import text
db = next(get_hospital_db('H001'))
try:
    db.execute(text('ALTER TABLE report_interpretation ADD COLUMN comparison_summary TEXT NULL AFTER summary_refs'))
    db.commit()
    print('comparison_summary added')
except Exception as e:
    print(f'comparison_summary: {e}')
try:
    db.execute(text('ALTER TABLE report_interpretation ADD COLUMN comparison_baseline_id BIGINT NULL AFTER comparison_summary'))
    db.commit()
    print('comparison_baseline_id added')
except Exception as e:
    print(f'comparison_baseline_id: {e}')
db.close()
"
```
Expected:打印 `comparison_summary added` 与 `comparison_baseline_id added`(或 "Duplicate column name" 表示已存在,等同成功)。

- [ ] **Step 3: 在 hospital_template 库执行同样迁移**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
from app.core.database import get_session
from sqlalchemy import text
db = get_session('hospital_template')
try:
    db.execute(text('ALTER TABLE report_interpretation ADD COLUMN comparison_summary TEXT NULL AFTER summary_refs'))
    db.execute(text('ALTER TABLE report_interpretation ADD COLUMN comparison_baseline_id BIGINT NULL AFTER comparison_summary'))
    db.commit()
    print('template done')
except Exception as e:
    print(f'template: {e}')
db.close()
"
```
Expected:打印 `template done`(或 Duplicate column,等同成功)。

- [ ] **Step 4: 修改 ORM 模型加字段**

`backend/app/modules/interpretation/models.py` 中 `ReportInterpretation` 在 `summary_refs` 与 `quality_note` 之间加两行:

```python
    summary_refs = Column(JSON, nullable=True)
    comparison_summary = Column(Text, nullable=True)
    comparison_baseline_id = Column(BigInteger, nullable=True)
    quality_note = Column(String(255), nullable=True)
```

- [ ] **Step 5: 校验 ORM 改动可正常 ORM-level 读写**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
from app.core.database import get_hospital_db
from app.modules.interpretation.models import ReportInterpretation
db = next(get_hospital_db('H001'))
row = db.query(ReportInterpretation).first()
if row:
    print('has attr comparison_summary:', hasattr(row, 'comparison_summary'))
    print('has attr comparison_baseline_id:', hasattr(row, 'comparison_baseline_id'))
    print('sample comparison_summary:', row.comparison_summary)
else:
    print('no rows (ok, schema check only)')
db.close()
"
```
Expected:`has attr comparison_summary: True`、`has attr comparison_baseline_id: True`,无 sqlalchemy 错误。

- [ ] **Step 6: 提交**

```bash
git add backend/scripts/manual_migrations/002_add_comparison_summary.sql \
        backend/app/modules/interpretation/models.py
git commit -m "feat(user-profile): add comparison_summary + comparison_baseline_id columns"
```

---

### Task 2: 纯函数模块 `comparison.py` + 完整单测(TDD)

**Files:**
- Create: `backend/app/modules/user_profile/__init__.py`
- Create: `backend/app/modules/user_profile/comparison.py`
- Create: `backend/tests/user_profile/__init__.py`
- Create: `backend/tests/user_profile/test_comparison.py`

**Interfaces:**
- Produces:`comparison.py` 暴露以下纯函数,后续 service 与 worker 钩子依赖:
  - `match_indicators(current: list[dict], baseline: list[dict]) -> list[dict]` —— 按 `item_name_standard` 优先、空值 fallback `item_name` 匹配两边指标,返回 `[{item_name_standard, item_name, current_value, baseline_value, unit, current_color, baseline_color}]`,单位不一致或双边都缺标准化名时按 `item_name` 严格匹配
  - `compute_delta(current_value: str, baseline_value: str) -> tuple[float, float] | None` —— 返回 `(delta, delta_pct)`,非数值返回 `None`
  - `judge_status(delta_pct: float) -> str` —— `<= -5` → `"improved"`,`>= 5` → `"worsened"`,其间 → `"stable"`
  - `trend_direction(points: list[dict]) -> str | None` —— `points[-1].value - points[-2].value > 0` → `"up"`,`< 0` → `"down"`,1 个点 → `None`
  - `build_comparison_prompt(current_report: dict, baseline_report: dict, indicators_diff: list[dict], top_abnormal: list[dict]) -> str` —— 拼出给 MedGo 的中文 prompt

- [ ] **Step 1: 创建测试包与测试文件,先写测试**

`backend/tests/user_profile/__init__.py`(空文件):

```python
```

`backend/tests/user_profile/test_comparison.py`:

```python
"""comparison.py 纯函数单测。无 DB 依赖。"""
import pytest

from app.modules.user_profile.comparison import (
    match_indicators,
    compute_delta,
    judge_status,
    trend_direction,
    build_comparison_prompt,
)


def test_match_indicators_by_standard():
    """item_name_standard 一致但原名不同,仍应匹配"""
    current = [
        {"item_name": "血糖", "item_name_standard": "空腹血糖", "result_value": "6.8",
         "unit": "mmol/L", "color_level": "red"},
    ]
    baseline = [
        {"item_name": "GLU", "item_name_standard": "空腹血糖", "result_value": "7.2",
         "unit": "mmol/L", "color_level": "red"},
    ]
    matches = match_indicators(current, baseline)
    assert len(matches) == 1
    assert matches[0]["item_name_standard"] == "空腹血糖"
    assert matches[0]["current_value"] == "6.8"
    assert matches[0]["baseline_value"] == "7.2"
    assert matches[0]["unit"] == "mmol/L"


def test_match_indicators_fallback_to_item_name_when_standard_missing():
    """双边 item_name_standard 都空,fallback item_name 严格匹配"""
    current = [
        {"item_name": "血压", "item_name_standard": None, "result_value": "145",
         "unit": "mmHg", "color_level": "red"},
    ]
    baseline = [
        {"item_name": "血压", "item_name_standard": None, "result_value": "130",
         "unit": "mmHg", "color_level": "green"},
    ]
    matches = match_indicators(current, baseline)
    assert len(matches) == 1
    assert matches[0]["current_value"] == "145"
    assert matches[0]["baseline_value"] == "130"


def test_match_indicators_only_baseline_standard_keeps_item_name_match():
    """一边有 standard 一边无 -> 不匹配(不强行 fallback)"""
    current = [
        {"item_name": "血糖", "item_name_standard": "空腹血糖", "result_value": "6.8",
         "unit": "mmol/L", "color_level": "red"},
    ]
    baseline = [
        {"item_name": "血糖", "item_name_standard": None, "result_value": "7.2",
         "unit": "mmol/L", "color_level": "red"},
    ]
    matches = match_indicators(current, baseline)
    assert len(matches) == 0


def test_compute_delta_numeric():
    assert compute_delta("6.8", "7.2") == (-0.4, pytest.approx(-5.56, rel=1e-2))


def test_compute_delta_non_numeric_returns_none():
    assert compute_delta("阳性", "阴性") is None
    assert compute_delta("++", "+") is None


def test_compute_delta_missing_value_returns_none():
    assert compute_delta("", "7.2") is None
    assert compute_delta("6.8", None) is None


def test_judge_status_thresholds():
    assert judge_status(-10) == "improved"
    assert judge_status(-5) == "improved"
    assert judge_status(-4.9) == "stable"
    assert judge_status(4.9) == "stable"
    assert judge_status(5) == "worsened"
    assert judge_status(15) == "worsened"


def test_trend_direction_up():
    points = [{"value": 5.0}, {"value": 6.5}]
    assert trend_direction(points) == "up"


def test_trend_direction_down():
    points = [{"value": 7.0}, {"value": 6.0}]
    assert trend_direction(points) == "down"


def test_trend_direction_single_point_is_none():
    assert trend_direction([{"value": 6.0}]) is None
    assert trend_direction([]) is None


def test_build_comparison_prompt_contains_key_sections():
    current_report = {"report_date": "2026-06-15", "overall_level": "yellow",
                      "red_count": 3, "yellow_count": 5, "green_count": 12}
    baseline_report = {"report_date": "2025-11-02", "overall_level": "red",
                       "red_count": 5, "yellow_count": 3, "green_count": 10}
    indicators_diff = [
        {"item_name": "血糖", "current_value": "6.8", "baseline_value": "7.2",
         "unit": "mmol/L", "current_color": "red", "delta": -0.4},
        {"item_name": "收缩压", "current_value": "145", "baseline_value": "130",
         "unit": "mmHg", "current_color": "red", "delta": 15},
    ]
    prompt = build_comparison_prompt(current_report, baseline_report, indicators_diff, indicators_diff)
    assert "2026-06-15" in prompt
    assert "2025-11-02" in prompt
    assert "血糖" in prompt
    assert "收缩压" in prompt
    assert "红区" in prompt
    assert "建议" in prompt
```

- [ ] **Step 2: 运行测试,确认全部失败(模块不存在)**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/user_profile/test_comparison.py -v
```
Expected:全部 ERROR / FAIL,报 `ModuleNotFoundError: No module named 'app.modules.user_profile'`。

- [ ] **Step 3: 创建模块包**

`backend/app/modules/user_profile/__init__.py`(空文件):

```python
```

- [ ] **Step 4: 实现 `comparison.py`**

```python
from typing import Optional


def _try_float(s) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def match_indicators(current: list[dict], baseline: list[dict]) -> list[dict]:
    """按 item_name_standard 优先匹配,双边都空时 fallback item_name。
    
    单边有 standard 一边无 -> 跳过(避免误匹配)。
    单位不一致仍匹配,由 compute_delta 时判断是否可计算。
    """
    matches = []
    cur_by_std = {r.get("item_name_standard"): r for r in current if r.get("item_name_standard")}
    base_by_std = {r.get("item_name_standard"): r for r in baseline if r.get("item_name_standard")}
    for std, c in cur_by_std.items():
        b = base_by_std.get(std)
        if b:
            matches.append({
                "item_name_standard": std,
                "item_name": c.get("item_name", std),
                "current_value": c.get("result_value"),
                "baseline_value": b.get("result_value"),
                "unit": c.get("unit"),
                "current_color": c.get("color_level"),
                "baseline_color": b.get("color_level"),
            })
    cur_no_std = [r for r in current if not r.get("item_name_standard")]
    base_no_std_by_name = {r.get("item_name"): r for r in baseline if not r.get("item_name_standard")}
    for c in cur_no_std:
        b = base_no_std_by_name.get(c.get("item_name"))
        if b:
            matches.append({
                "item_name_standard": None,
                "item_name": c.get("item_name"),
                "current_value": c.get("result_value"),
                "baseline_value": b.get("result_value"),
                "unit": c.get("unit"),
                "current_color": c.get("color_level"),
                "baseline_color": b.get("color_level"),
            })
    return matches


def compute_delta(current_value: str, baseline_value: str) -> Optional[tuple[float, float]]:
    """返回 (delta, delta_pct)。非数值返回 None。
    
    delta_pct = (current - baseline) / |baseline| * 100,baseline=0 时返回 None。
    """
    c = _try_float(current_value)
    b = _try_float(baseline_value)
    if c is None or b is None:
        return None
    if b == 0:
        return None
    delta = round(c - b, 4)
    delta_pct = round((c - b) / abs(b) * 100, 2)
    return delta, delta_pct


def judge_status(delta_pct: float) -> str:
    if delta_pct <= -5:
        return "improved"
    if delta_pct >= 5:
        return "worsened"
    return "stable"


def trend_direction(points: list[dict]) -> Optional[str]:
    if len(points) < 2:
        return None
    last = _try_float(points[-1].get("value"))
    prev = _try_float(points[-2].get("value"))
    if last is None or prev is None:
        return None
    if last > prev:
        return "up"
    if last < prev:
        return "down"
    return None


def build_comparison_prompt(current_report: dict, baseline_report: dict,
                            indicators_diff: list[dict], top_abnormal: list[dict]) -> str:
    """拼出给 MedGo 的中文 prompt。indicators_diff 与 top_abnormal 在 worker 钩子里通常是同一份数据。"""
    cur_level = current_report.get("overall_level") or "未知"
    base_level = baseline_report.get("overall_level") or "未知"
    
    abnormal_lines = []
    for ind in top_abnormal[:5]:
        name = ind.get("item_name") or ind.get("item_name_standard") or ""
        cur_v = ind.get("current_value", "")
        unit = ind.get("unit", "")
        cur_color = ind.get("current_color") or ""
        base_v = ind.get("baseline_value", "")
        delta = ind.get("delta")
        arrow = ""
        if delta is not None:
            arrow = f",上次{base_v}," + ("↑" if delta > 0 else "↓") + f"{abs(delta)}"
        abnormal_lines.append(
            f"  - {name}:{cur_v} {unit}({cur_color or '未判色'}{arrow})"
        )
    abnormal_text = "\n".join(abnormal_lines) or "  (无异常指标)"
    
    return f"""你是体检报告解读助手。基于下方两份报告的对比数据,用通俗易懂的中文写一段健康变化小结(150-250字)。

## 本次报告({current_report.get('report_date', '未知')})
- 总体:{cur_level} | 红区{current_report.get('red_count', 0)} 黄区{current_report.get('yellow_count', 0)} 绿区{current_report.get('green_count', 0)}
- 异常指标:
{abnormal_text}

## 上一份报告({baseline_report.get('report_date', '未知')})
- 总体:{base_level} | 红区{baseline_report.get('red_count', 0)} 黄区{baseline_report.get('yellow_count', 0)} 绿区{baseline_report.get('green_count', 0)}

## 小结要求
1. 先说整体变化(红黄区数量变化、新增/消失的异常)
2. 再点出明显改善和明显恶化的指标
3. 给出 1-2 条针对性建议(基于上述指标,不编造)
4. 不下诊断,语气同解读模块
5. 不输出 thinking 标签
"""
```

- [ ] **Step 5: 运行测试,确认全部通过**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/user_profile/test_comparison.py -v
```
Expected:11 passed。

- [ ] **Step 6: 提交**

```bash
git add backend/app/modules/user_profile/__init__.py \
        backend/app/modules/user_profile/comparison.py \
        backend/tests/user_profile/__init__.py \
        backend/tests/user_profile/test_comparison.py
git commit -m "feat(user-profile): add pure comparison functions + tests"
```

---

### Task 3: `service.py` — 数据库查询与 worker 钩子

**Files:**
- Create: `backend/app/modules/user_profile/service.py`
- Create: `backend/tests/user_profile/test_service.py`

**Interfaces:**
- Consumes: `comparison.py`(Task 2)、`interpretation/models.py::ReportInterpretation`(Task 1)、`report/models.py::ReportInfo/ReportIndicator`、`ai/llm.py::get_chat_model`、`ai/agents/think_filter.py::strip_think_tags`
- Produces:
  - `get_overview(db: Session, user_id: int) -> dict` —— 档案页接口数据
  - `get_comparison(db: Session, user_id: int, report_id: int, baseline_id: int | None = None) -> dict` —— 对比接口数据(包含 ai_summary,从缓存读)
  - `get_ai_summary(db: Session, user_id: int, report_id: int, baseline_id: int) -> tuple[str, bool]` —— 返回 `(summary, cached)`
  - `try_generate_comparison_summary(db: Session, report_id: int) -> None` —— worker 钩子,失败仅记 warning
  - `_auto_select_baseline(db: Session, user_id: int, report_id: int) -> ReportInfo | None` —— 私有辅助

- [ ] **Step 1: 先写测试**

`backend/tests/user_profile/test_service.py`:

```python
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
```

- [ ] **Step 2: 运行测试,确认失败**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/user_profile/test_service.py -v
```
Expected:全部 ERROR / FAIL,报 `ModuleNotFoundError: No module named 'app.modules.user_profile.service'`。

- [ ] **Step 3: 实现 `service.py`**

```python
import logging
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.report.models import ReportInfo, ReportIndicator
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
from app.modules.user_profile.comparison import (
    match_indicators, compute_delta, judge_status, trend_direction,
    build_comparison_prompt,
)

logger = logging.getLogger(__name__)

MAX_HISTORY_REPORTS = 100
TOP_INDICATORS_DEFAULT = 10
TOP_ABNORMAL_FOR_PROMPT = 5
STATUS_STABLE_PCT = 5


def _auto_select_baseline(db: Session, user_id: int, report_id: int) -> Optional[ReportInfo]:
    """选 report_date 早于本报告且最接近的那一份,fallback created_at。"""
    current = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not current:
        return None
    q = db.query(ReportInfo).filter(
        ReportInfo.user_id == user_id,
        ReportInfo.id != report_id,
    )
    if current.report_date:
        q = q.filter(ReportInfo.report_date < current.report_date)
        q = q.order_by(ReportInfo.report_date.desc())
    else:
        q = q.order_by(ReportInfo.created_at.desc())
    return q.first()


def get_overview(db: Session, user_id: int) -> dict:
    """档案页主数据:总览 + 指标走势 + 异常分布。"""
    reports = db.query(ReportInfo).filter(
        ReportInfo.user_id == user_id,
    ).order_by(ReportInfo.report_date.asc()).all()
    if not reports:
        return {"user_summary": None, "indicator_trends": [], "abnormal_distribution": []}
    
    report_ids = [r.id for r in reports]
    indicators = db.query(ReportIndicator).filter(
        ReportIndicator.report_id.in_(report_ids),
    ).all()
    report_map = {r.id: r for r in reports}
    
    judgments_by_indicator_id = {}
    if indicators:
        judgments = db.query(IndicatorJudgment).filter(
            IndicatorJudgment.indicator_id.in_([i.id for i in indicators]),
        ).all()
        judgments_by_indicator_id = {j.indicator_id: j for j in judgments}
    
    by_key = {}
    for ind in indicators:
        try:
            float(str(ind.result_value).strip())
        except (TypeError, ValueError):
            continue
        key = ind.item_name_standard or ind.item_name
        if not key:
            continue
        if key not in by_key:
            by_key[key] = {
                "item_name_standard": ind.item_name_standard,
                "item_name": ind.item_name,
                "unit": ind.unit,
                "points": [],
            }
        judgment = judgments_by_indicator_id.get(ind.id)
        by_key[key]["points"].append({
            "report_id": ind.report_id,
            "report_date": report_map[ind.report_id].report_date.isoformat() if report_map[ind.report_id].report_date else None,
            "value": float(str(ind.result_value).strip()),
            "color": judgment.color_level if judgment else None,
        })
    
    for v in by_key.values():
        v["trend_direction"] = trend_direction(v["points"])
        v["latest_deviation"] = v["points"][-1].get("color") if v["points"] else None
    
    abnormal_dist_q = text("""
        SELECT ij.item_name, ij.item_name_standard, ij.color_level, COUNT(*) as cnt
        FROM indicator_judgment ij
        JOIN report_interpretation ri2 ON ij.interpretation_id = ri2.id
        JOIN report_info ri ON ri2.report_id = ri.id
        WHERE ri.user_id = :uid AND ij.color_level IN ('red', 'yellow')
        GROUP BY ij.item_name, ij.item_name_standard, ij.color_level
    """)
    rows = db.execute(abnormal_dist_q, {"uid": user_id}).fetchall()
    grouped = {}
    for r in rows:
        key = r.item_name_standard or r.item_name
        if not key:
            continue
        if key not in grouped:
            grouped[key] = {"item_name_standard": key, "red_count": 0, "yellow_count": 0, "last_color": "green"}
        if r.color_level == "red":
            grouped[key]["red_count"] += r.cnt
        elif r.color_level == "yellow":
            grouped[key]["yellow_count"] += r.cnt
        if r.color_level in ("red", "yellow"):
            grouped[key]["last_color"] = r.color_level
    abnormal_distribution = sorted(
        grouped.values(),
        key=lambda x: (x["red_count"], x["yellow_count"]),
        reverse=True,
    )[:20]
    
    latest = reports[-1]
    latest_interp = db.query(ReportInterpretation).filter_by(report_id=latest.id).first()
    summary = {
        "total_reports": len(reports),
        "earliest_date": reports[0].report_date.isoformat() if reports[0].report_date else None,
        "latest_date": latest.report_date.isoformat() if latest.report_date else None,
        "latest_overall_level": latest_interp.overall_level if latest_interp else None,
        "latest_red": latest_interp.red_count if latest_interp else 0,
        "latest_yellow": latest_interp.yellow_count if latest_interp else 0,
        "latest_green": latest_interp.green_count if latest_interp else 0,
        "baseline_date": None,
    }
    baseline = _auto_select_baseline(db, user_id, latest.id)
    if baseline:
        summary["baseline_date"] = baseline.report_date.isoformat() if baseline.report_date else None
    
    trends_sorted = sorted(
        by_key.values(),
        key=lambda x: (
            0 if x.get("latest_deviation") in ("red", "yellow") else 1,
            -abs(max([p["value"] for p in x["points"]], default=0) - min([p["value"] for p in x["points"]], default=0)),
        ),
    )
    return {
        "user_summary": summary,
        "indicator_trends": trends_sorted,
        "abnormal_distribution": abnormal_distribution,
    }


def _build_indicator_diff(db: Session, current: ReportInfo, baseline: ReportInfo) -> dict:
    """组装对比明细(current / baseline / delta_summary / indicators / only_in_*)。"""
    cur_inds = db.query(ReportIndicator).filter_by(report_id=current.id).all()
    base_inds = db.query(ReportIndicator).filter_by(report_id=baseline.id).all()
    
    cur_judgments = {j.indicator_id: j for j in db.query(IndicatorJudgment).join(
        ReportInterpretation, IndicatorJudgment.interpretation_id == ReportInterpretation.id
    ).filter(ReportInterpretation.report_id == current.id).all()}
    base_judgments = {j.indicator_id: j for j in db.query(IndicatorJudgment).join(
        ReportInterpretation, IndicatorJudgment.interpretation_id == ReportInterpretation.id
    ).filter(ReportInterpretation.report_id == baseline.id).all()}
    
    cur_dicts = [
        {**_indicator_to_dict(i), "color_level": cur_judgments.get(i.id, MagicMock(color_level=None)).color_level if cur_judgments.get(i.id) else None}
        for i in cur_inds
    ]
    base_dicts = [
        {**_indicator_to_dict(i), "color_level": base_judgments.get(i.id, MagicMock(color_level=None)).color_level if base_judgments.get(i.id) else None}
        for i in base_inds
    ]
    
    matches = match_indicators(cur_dicts, base_dicts)
    indicators_diff = []
    cur_keys = {(m["item_name_standard"], m["item_name"]) for m in matches}
    base_keys = {(m["item_name_standard"], m["item_name"]) for m in matches}
    only_in_current = []
    only_in_baseline = []
    
    for ind in cur_inds:
        key = (ind.item_name_standard or None, ind.item_name)
        if key not in cur_keys:
            try:
                float(str(ind.result_value).strip())
                only_in_current.append({
                    "item_name": ind.item_name,
                    "item_name_standard": ind.item_name_standard,
                    "current_value": ind.result_value,
                    "unit": ind.unit,
                })
            except (TypeError, ValueError):
                only_in_current.append({
                    "item_name": ind.item_name,
                    "item_name_standard": ind.item_name_standard,
                    "current_value": ind.result_value,
                    "unit": ind.unit,
                })
    for ind in base_inds:
        key = (ind.item_name_standard or None, ind.item_name)
        if key not in base_keys:
            only_in_baseline.append({
                "item_name": ind.item_name,
                "item_name_standard": ind.item_name_standard,
                "baseline_value": ind.result_value,
                "unit": ind.unit,
            })
    
    for m in matches:
        delta = compute_delta(m["current_value"], m["baseline_value"])
        entry = {
            "item_name_standard": m["item_name_standard"],
            "item_name": m["item_name"],
            "current_value": m["current_value"],
            "baseline_value": m["baseline_value"],
            "unit": m["unit"],
            "current_color": m["current_color"],
            "baseline_color": m["baseline_color"],
            "delta": None,
            "delta_pct": None,
            "status": None,
        }
        if delta is not None:
            entry["delta"], entry["delta_pct"] = delta
            entry["status"] = judge_status(delta[1])
        indicators_diff.append(entry)
    
    cur_interp = db.query(ReportInterpretation).filter_by(report_id=current.id).first()
    base_interp = db.query(ReportInterpretation).filter_by(report_id=baseline.id).first()
    
    return {
        "current": {
            "report_id": current.id,
            "report_date": current.report_date.isoformat() if current.report_date else None,
            "overall_level": cur_interp.overall_level if cur_interp else None,
            "red_count": cur_interp.red_count if cur_interp else 0,
            "yellow_count": cur_interp.yellow_count if cur_interp else 0,
            "green_count": cur_interp.green_count if cur_interp else 0,
        },
        "baseline": {
            "report_id": baseline.id,
            "report_date": baseline.report_date.isoformat() if baseline.report_date else None,
            "overall_level": base_interp.overall_level if base_interp else None,
            "red_count": base_interp.red_count if base_interp else 0,
            "yellow_count": base_interp.yellow_count if base_interp else 0,
            "green_count": base_interp.green_count if base_interp else 0,
        },
        "delta_summary": {
            "red_delta": (cur_interp.red_count if cur_interp else 0) - (base_interp.red_count if base_interp else 0),
            "yellow_delta": (cur_interp.yellow_count if cur_interp else 0) - (base_interp.yellow_count if base_interp else 0),
            "green_delta": (cur_interp.green_count if cur_interp else 0) - (base_interp.green_count if base_interp else 0),
        },
        "indicators": indicators_diff,
        "only_in_current": only_in_current,
        "only_in_baseline": only_in_baseline,
        "_current_report_obj": current,
        "_baseline_report_obj": baseline,
        "_current_interp": cur_interp,
    }


def _indicator_to_dict(ind):
    return {
        "item_name": ind.item_name,
        "item_name_standard": ind.item_name_standard,
        "result_value": ind.result_value,
        "unit": ind.unit,
    }


def _filter_abnormal_top(diff_result: dict) -> list[dict]:
    """筛出给 prompt 用的 top 异常指标 (red 优先, 黄次之, 同色 |delta| 降序)。"""
    indicators = diff_result["indicators"]
    def sort_key(x):
        cur_color = x.get("current_color") or "green"
        color_pri = 0 if cur_color == "red" else (1 if cur_color == "yellow" else 2)
        delta_abs = abs(x.get("delta") or 0)
        return (color_pri, -delta_abs)
    return sorted(indicators, key=sort_key)[:TOP_ABNORMAL_FOR_PROMPT]


def get_comparison(db: Session, user_id: int, report_id: int,
                   baseline_id: Optional[int] = None) -> dict:
    """对比接口主入口。附带 ai_summary(走缓存命中逻辑)。"""
    current = db.query(ReportInfo).filter_by(id=report_id, user_id=user_id).first()
    if not current:
        return {}
    if baseline_id:
        baseline = db.query(ReportInfo).filter_by(id=baseline_id, user_id=user_id).first()
        if not baseline:
            return {}
    else:
        baseline = _auto_select_baseline(db, user_id, report_id)
        if not baseline:
            diff = {
                "current": {
                    "report_id": current.id,
                    "report_date": current.report_date.isoformat() if current.report_date else None,
                    "overall_level": None, "red_count": 0, "yellow_count": 0, "green_count": 0,
                },
                "baseline": None,
                "delta_summary": {"red_delta": 0, "yellow_delta": 0, "green_delta": 0},
                "indicators": [],
                "only_in_current": [],
                "only_in_baseline": [],
                "ai_summary": "",
                "ai_summary_cached": False,
            }
            return diff
    
    diff = _build_indicator_diff(db, current, baseline)
    interp = diff.get("_current_interp")
    ai_summary = ""
    cached = False
    if interp:
        if interp.comparison_summary and interp.comparison_baseline_id == baseline.id:
            ai_summary = interp.comparison_summary or ""
            cached = True
    
    diff_out = {k: v for k, v in diff.items() if not k.startswith("_")}
    diff_out["ai_summary"] = ai_summary
    diff_out["ai_summary_cached"] = cached
    return diff_out


def get_ai_summary(db: Session, user_id: int, report_id: int, baseline_id: int) -> tuple[str, bool]:
    """读缓存或调 LLM 实时生成。实时生成不写回缓存。"""
    current = db.query(ReportInfo).filter_by(id=report_id, user_id=user_id).first()
    baseline = db.query(ReportInfo).filter_by(id=baseline_id, user_id=user_id).first()
    if not current or not baseline:
        return "", False
    interp = db.query(ReportInterpretation).filter_by(report_id=report_id).first()
    if interp and interp.comparison_summary and interp.comparison_baseline_id == baseline_id:
        return interp.comparison_summary, True
    
    diff = _build_indicator_diff(db, current, baseline)
    top_abnormal = _filter_abnormal_top(diff)
    prompt = build_comparison_prompt(
        diff["current"], diff["baseline"], diff["indicators"], top_abnormal,
    )
    summary = _call_llm_for_summary(prompt)
    return summary, False


def _call_llm_for_summary(prompt: str) -> str:
    """调用 MedGo 生成小结。失败返回空串并记 warning。"""
    from app.ai.llm import get_chat_model
    from app.ai.agents.think_filter import strip_think_tags
    try:
        model = get_chat_model(streaming=False)
        resp = model.invoke([("user", prompt)], max_tokens=512)
        return strip_think_tags(resp.content or "")
    except Exception as e:
        logger.warning("comparison summary LLM call failed: %s", e)
        return ""


def try_generate_comparison_summary(db: Session, report_id: int) -> None:
    """worker 钩子:解读完成后调一次,生成 AI 小结并写回缓存。
    
    - 用户历史报告不足 2 份 -> 跳过
    - 缓存已有且 baseline 匹配 -> 跳过
    - LLM 失败 -> logger.warning,不报错,不阻塞主流程
    """
    from app.ai.llm import get_chat_model
    from app.ai.agents.think_filter import strip_think_tags
    
    current = db.query(ReportInfo).filter_by(id=report_id).first()
    if not current:
        return
    interp = db.query(ReportInterpretation).filter_by(report_id=report_id).first()
    if not interp:
        return
    if interp.comparison_summary and interp.comparison_baseline_id:
        return
    
    baseline = _auto_select_baseline(db, current.user_id, report_id)
    if not baseline:
        return
    
    diff = _build_indicator_diff(db, current, baseline)
    top_abnormal = _filter_abnormal_top(diff)
    prompt = build_comparison_prompt(
        diff["current"], diff["baseline"], diff["indicators"], top_abnormal,
    )
    try:
        model = get_chat_model(streaming=False)
        resp = model.invoke([("user", prompt)], max_tokens=512)
        summary = strip_think_tags(resp.content or "")
        if summary:
            interp.comparison_summary = summary
            interp.comparison_baseline_id = baseline.id
            db.commit()
    except Exception as e:
        logger.warning("comparison summary generation failed: %s", e)
```

- [ ] **Step 4: 运行测试**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/user_profile/test_service.py -v
```
Expected:6 passed。如果有失败,根据失败信息修正实现或测试(典型问题:in-memory SQLite 不支持某些 MySQL 函数 —— 上述 SQL 测试用例只用纯 ORM,不应触发方言冲突)。

- [ ] **Step 5: 跑整个 user_profile 测试包验证**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/user_profile -v
```
Expected:17 passed(11 + 6)。

- [ ] **Step 6: 提交**

```bash
git add backend/app/modules/user_profile/service.py \
        backend/tests/user_profile/test_service.py
git commit -m "feat(user-profile): add service layer with worker hook for comparison summary"
```

---

### Task 4: `router.py` — 暴露 3 个 HTTP 接口并挂到 `main.py`

**Files:**
- Create: `backend/app/modules/user_profile/router.py`
- Modify: `backend/app/main.py:14,39` (加 import 与 include_router)

**Interfaces:**
- Consumes:`service.get_overview` / `get_comparison` / `get_ai_summary`(Task 3);`core.dependencies.get_current_user` / `CurrentUser`;`core.database.get_hospital_db`;`utils.exceptions.NotFoundException` / `ValidationException`
- Produces:HTTP 接口
  - `GET /profile/overview`
  - `GET /profile/compare?report_id=X&baseline_id=Y`
  - `GET /profile/ai-summary?report_id=X&baseline_id=Y`

- [ ] **Step 1: 写 router.py**

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.user_profile import service

router = APIRouter()


def _get_db(current_user: CurrentUser = Depends(get_current_user)):
    if not current_user.hospital_id:
        raise ValidationException(detail="Hospital context required")
    gen = get_hospital_db(current_user.hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


@router.get("/overview")
def overview(
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return service.get_overview(db, current_user.user_id)


@router.get("/compare")
def compare(
    report_id: int = Query(...),
    baseline_id: Optional[int] = Query(None),
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    result = service.get_comparison(db, current_user.user_id, report_id, baseline_id)
    if not result:
        raise NotFoundException(detail="Report not found")
    return result


@router.get("/ai-summary")
def ai_summary(
    report_id: int = Query(...),
    baseline_id: int = Query(...),
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    summary, cached = service.get_ai_summary(db, current_user.user_id, report_id, baseline_id)
    return {"ai_summary": summary, "cached": cached}
```

- [ ] **Step 2: 修改 `main.py` 注册路由**

在 `backend/app/main.py` 第 14 行后加:

```python
from app.modules.chat.router import router as chat_router
from app.modules.user_profile.router import router as user_profile_router
```

(放在 chat_router import 下一行,保持模块导入顺序。)

第 39 行后加 include:

```python
    app.include_router(chat_router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(user_profile_router, prefix="/api/v1/profile", tags=["user-profile"])
```

具体两处编辑:

a) import 段 —— 在 `from app.modules.chat.router import router as chat_router` 这一行后插入:

```python
from app.modules.user_profile.router import router as user_profile_router
```

b) 注册段 —— 在 `app.include_router(chat_router, prefix="/api/v1/chat", tags=["chat"])` 后插入:

```python
    app.include_router(user_profile_router, prefix="/api/v1/profile", tags=["user-profile"])
```

- [ ] **Step 3: 起一个临时进程验证路由注册成功**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
from app.main import app
routes = [r.path for r in app.routes if hasattr(r, 'path')]
profile_routes = [p for p in routes if p.startswith('/api/v1/profile')]
print('profile routes:', profile_routes)
assert '/api/v1/profile/overview' in profile_routes
assert '/api/v1/profile/compare' in profile_routes
assert '/api/v1/profile/ai-summary' in profile_routes
print('OK')
"
```
Expected:`profile routes: ['/api/v1/profile/overview', '/api/v1/profile/compare', '/api/v1/profile/ai-summary']` 和 `OK`。

- [ ] **Step 4: 跑全量 user_profile 测试再确认**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/user_profile -v
```
Expected:17 passed(路由不影响纯单测)。

- [ ] **Step 5: 提交**

```bash
git add backend/app/modules/user_profile/router.py backend/app/main.py
git commit -m "feat(user-profile): expose 3 HTTP endpoints on /api/v1/profile"
```

---

### Task 5: 改造 `interpretation/worker.py` —— 加 worker 钩子

**Files:**
- Modify: `backend/app/modules/interpretation/worker.py:6-22`

**Interfaces:**
- Consumes:`service.try_generate_comparison_summary`(Task 3)
- 修改 `handle_interpretation_task` 函数:`run_interpretation_agent` 调用之后(成功路径),追加生成对比小结

- [ ] **Step 1: 修改 worker.py**

原文件:

```python
from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq
from app.ai.agents import run_interpretation_agent


def handle_interpretation_task(message: dict):
    payload = message.get("payload", {})
    if payload.get("event"):
        return
    report_id = payload.get("report_id")
    hospital_id = payload.get("hospital_id")

    if not report_id:
        return

    db = next(get_hospital_db(hospital_id))
    try:
        run_interpretation_agent(hospital_id, db, report_id)
    except Exception as e:
        print(f"Interpretation failed for report {report_id}: {e}")
    finally:
        db.close()
```

改为(仅在 run_interpretation_agent 成功后追加钩子,异常路径不变):

```python
from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq
from app.ai.agents import run_interpretation_agent


def handle_interpretation_task(message: dict):
    payload = message.get("payload", {})
    if payload.get("event"):
        return
    report_id = payload.get("report_id")
    hospital_id = payload.get("hospital_id")

    if not report_id:
        return

    db = next(get_hospital_db(hospital_id))
    try:
        run_interpretation_agent(hospital_id, db, report_id)
        try:
            from app.modules.user_profile.service import try_generate_comparison_summary
            try_generate_comparison_summary(db, report_id)
        except Exception as e:
            print(f"Comparison summary failed for report {report_id}: {e}")
    except Exception as e:
        print(f"Interpretation failed for report {report_id}: {e}")
    finally:
        db.close()


def start_worker():
    while True:
        try:
            rabbitmq.consume("interpretation.urgent", handle_interpretation_task)
            rabbitmq.consume("interpretation.normal", handle_interpretation_task)
            print("Interpretation worker started, waiting for tasks...")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Worker disconnected: {e}, reconnecting in 3s...")
            import time
            time.sleep(3)
```

关键改动:仅在 `run_interpretation_agent` 调用返回后,内嵌一层 try/except 调 `try_generate_comparison_summary`。内部失败只打印 log,不影响主流程。

- [ ] **Step 2: 校验 import 无循环**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
from app.modules.interpretation.worker import handle_interpretation_task
print('import ok')
"
```
Expected:打印 `import ok`(因 `try_generate_comparison_summary` 走函数内 lazy import,且 `user_profile.service` 没反向 import interpretation.worker,无循环)。

- [ ] **Step 3: 跑全量测试再确认**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/user_profile tests/ai/agents -v
```
Expected:user_profile 17 passed,ai/agents 测试无回归。

- [ ] **Step 4: 提交**

```bash
git add backend/app/modules/interpretation/worker.py
git commit -m "feat(user-profile): wire comparison summary generation into interpretation worker"
```

---

### Task 6: 前端 `IndicatorTrendChart.tsx` —— 数值型折线图

**Files:**
- Create: `frontend/packages/user-portal/src/components/IndicatorTrendChart.tsx`

**Interfaces:**
- Produces:React 组件 `IndicatorTrendChart({ data: TrendPoint[] })`,TrendPoint = `{ report_id: number; report_date: string; value: number; color?: string }`,`data.length < 2` 返回 null;轻量 SVG polyline(无 chart 库依赖)

- [ ] **Step 1: 写组件**

```tsx
interface TrendPoint {
  report_id: number;
  report_date: string;
  value: number;
  color?: string | null;
}

const COLOR_HEX: Record<string, string> = {
  red: '#ef4444',
  yellow: '#f59e0b',
  green: '#10b981',
};

export default function IndicatorTrendChart({ data }: { data: TrendPoint[] }) {
  if (!data || data.length < 2) return null;

  const W = 220;
  const H = 50;
  const PAD_X = 8;
  const PAD_Y = 8;
  const values = data.map(d => d.value);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = maxV - minV || 1;

  const xStep = (W - 2 * PAD_X) / (data.length - 1);
  const yOf = (v: number) => H - PAD_Y - ((v - minV) / span) * (H - 2 * PAD_Y);
  const points = data.map((d, i) => `${PAD_X + i * xStep},${yOf(d.value)}`).join(' ');

  return (
    <svg width={W} height={H} style={{ display: 'block', marginTop: 4 }}>
      <polyline
        points={points}
        fill="none"
        stroke="var(--color-primary, #0D9488)"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {data.map((d, i) => {
        const cx = PAD_X + i * xStep;
        const cy = yOf(d.value);
        const fill = d.color ? (COLOR_HEX[d.color] || '#0D9488') : '#0D9488';
        const dateLabel = d.report_date ? d.report_date.slice(5) : '';
        return (
          <g key={d.report_id ?? i}>
            <circle cx={cx} cy={cy} r="3" fill={fill} stroke="#fff" strokeWidth="1" />
            <text x={cx} y={H - 1} fontSize="8" fill="var(--color-text-secondary, #888)" textAnchor="middle">
              {dateLabel}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
```

- [ ] **Step 2: 验证 TypeScript 类型与构建**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && npm run build -w @hospital/user-portal
```
Expected:无 TS 错误,构建通过(组件尚未被引用,不会影响产物包,但 lint/build 会扫所有文件)。

- [ ] **Step 3: 提交**

```bash
git add frontend/packages/user-portal/src/components/IndigentTrendChart.tsx \
        frontend/packages/user-portal/src/components/IndicatorTrendChart.tsx
git commit -m "feat(user-portal): add IndicatorTrendChart for numeric trend visualization"
```
(注意:第一次提交时如 git 报 "did not match any files",移除 IndigentTrendChart 那条路径,只 add IndicatorTrendChart.tsx)

---

### Task 7: 前端 `ComparisonCard.tsx` —— 报告对比卡

**Files:**
- Create: `frontend/packages/user-portal/src/components/ComparisonCard.tsx`

**Interfaces:**
- Produces:`ComparisonCard({ reportId, baselineId }: { reportId: number; baselineId?: number })`,内部通过 `api.get('/profile/compare', ...)` 取数据,本地 state 缓存;基准 Select 切换时仅调 `ai-summary` 接口刷新小结段
- Consumes:`useUserStore` 拿 `api`,antd `Select` / `Spin` / `message`

- [ ] **Step 1: 写组件**

```tsx
import { useEffect, useState } from 'react';
import { Select, Spin, message } from 'antd';
import { useUserStore } from '../stores/userStore';

interface CompareData {
  current: { report_id: number; report_date: string; overall_level: string;
    red_count: number; yellow_count: number; green_count: number; } | null;
  baseline: { report_id: number; report_date: string; overall_level: string;
    red_count: number; yellow_count: number; green_count: number; } | null;
  delta_summary: { red_delta: number; yellow_delta: number; green_delta: number };
  indicators: Array<{
    item_name: string; current_value: string; baseline_value: string;
    unit: string; current_color: string; baseline_color: string;
    delta: number | null; delta_pct: number | null; status: string | null;
  }>;
  only_in_current: Array<{ item_name: string }>;
  only_in_baseline: Array<{ item_name: string }>;
  ai_summary: string;
  ai_summary_cached: boolean;
}

interface HistoryItem { id: number; report_date?: string; name?: string; created_at?: string; }

function DeltaBadge({ delta }: { delta: number }) {
  if (delta === 0) return <span style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}>-</span>;
  const isUp = delta > 0;
  return (
    <span style={{
      fontSize: 12, fontWeight: 600,
      color: isUp ? 'var(--color-red)' : 'var(--color-green)',
    }}>
      {isUp ? '↑' : '↓'}{Math.abs(delta)}
    </span>
  );
}

function StatusTag({ status }: { status: string | null }) {
  if (!status) return null;
  const map: Record<string, string> = {
    improved: 'var(--color-green)',
    worsened: 'var(--color-red)',
    stable: 'var(--color-text-secondary)',
  };
  const labelMap: Record<string, string> = { improved: '改善', worsened: '恶化', stable: '持平' };
  return <span style={{ fontSize: 11, color: map[status] || '#888' }}>{labelMap[status]}</span>;
}

export default function ComparisonCard({ reportId, baselineId: initialBaseline }: {
  reportId: number; baselineId?: number;
}) {
  const { api } = useUserStore();
  const [data, setData] = useState<CompareData | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [currentBaseline, setCurrentBaseline] = useState<number | undefined>(initialBaseline);
  const [loading, setLoading] = useState(true);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [summaryExpanded, setSummaryExpanded] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.get('/profile/compare', { params: { report_id: reportId, baseline_id: currentBaseline } })
      .then(r => {
        setData(r.data);
        if (r.data?.baseline?.report_id && currentBaseline === undefined) {
          setCurrentBaseline(r.data.baseline.report_id);
        }
      })
      .catch(() => { setData(null); })
      .finally(() => setLoading(false));
    api.get('/reports').then(r => setHistory(r.data.items || [])).catch(() => {});
  }, [reportId]);

  const switchBaseline = async (id: number) => {
    setCurrentBaseline(id);
    if (!data) return;
    setSummaryLoading(true);
    try {
      const r = await api.get('/profile/ai-summary', { params: { report_id: reportId, baseline_id: id } });
      setData({ ...data, ai_summary: r.data.ai_summary || '', ai_summary_cached: r.data.cached });
    } catch {
      message.error('AI 小结切换失败');
    } finally {
      setSummaryLoading(false);
    }
  };

  if (loading) return <div style={{ textAlign: 'center', padding: 16 }}><Spin /></div>;
  if (!data || !data.baseline) return null;

  const histOptions = history
    .filter(h => h.id !== reportId && (!data.baseline || h.id !== data.baseline.report_id))
    .map(h => ({
      value: h.id,
      label: h.report_date ? `${h.report_date}${h.name ? ' · ' + h.name : ''}` : `报告 ${h.id}`,
    }));
  const baseOpt = data.baseline ? [{
    value: data.baseline.report_id,
    label: data.baseline.report_date
      ? `${data.baseline.report_date}${data.baseline.overall_level ? ' · ' + data.baseline.overall_level : ''}`
      : `报告 ${data.baseline.report_id}`,
  }] : [];
  const allOptions = [...baseOpt, ...histOptions];

  const indicatorsToShow = expanded ? data.indicators : data.indicators.slice(0, 6);

  return (
    <div style={{
      background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
      padding: 16, boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
      marginBottom: 20,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <span style={{ fontWeight: 600, fontSize: 14 }}>📊 与上次报告对比</span>
        <Select
          size="small" value={currentBaseline} style={{ width: 180 }}
          onChange={switchBaseline} options={allOptions} loading={summaryLoading}
        />
      </div>

      <div style={{
        display: 'flex', gap: 12, padding: '8px 12px', background: 'var(--color-bg)',
        borderRadius: 'var(--radius-sm)', marginBottom: 12, fontSize: 12,
      }}>
        <span>红区 <b style={{ color: 'var(--color-red)' }}>{data.baseline.red_count}</b> →
          <b style={{ color: 'var(--color-red)' }}>{data.current.red_count}</b>
          <DeltaBadge delta={data.delta_summary.red_delta} />
        </span>
        <span>黄区 <b style={{ color: 'var(--color-yellow)' }}>{data.baseline.yellow_count}</b> →
          <b style={{ color: 'var(--color-yellow)' }}>{data.current.yellow_count}</b>
          <DeltaBadge delta={data.delta_summary.yellow_delta} />
        </span>
        <span>绿区 <b style={{ color: 'var(--color-green)' }}>{data.baseline.green_count}</b> →
          <b style={{ color: 'var(--color-green)' }}>{data.current.green_count}</b>
          <DeltaBadge delta={data.delta_summary.green_delta} />
        </span>
      </div>

      {data.indicators.length > 0 && (
        <div>
          <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginBottom: 6 }}>
            指标差异
          </div>
          {indicatorsToShow.map((ind, i) => (
            <div key={i} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '8px 0', borderBottom: '1px solid var(--color-border-light)',
              fontSize: 12,
            }}>
              <span style={{ flex: 1, fontWeight: 500 }}>{ind.item_name}</span>
              <span style={{ color: 'var(--color-text-secondary)' }}>
                {ind.baseline_value} → <b style={{ color: 'var(--color-text)' }}>{ind.current_value}</b>
                {ind.unit ? ` ${ind.unit}` : ''}
              </span>
              <span style={{ marginLeft: 12, minWidth: 56, textAlign: 'right' }}>
                {ind.delta !== null ? (
                  <>
                    <span style={{
                      color: ind.delta > 0 ? 'var(--color-red)' : 'var(--color-green)',
                      fontWeight: 600,
                    }}>
                      {ind.delta > 0 ? '+' : ''}{ind.delta}
                    </span>{' '}
                    <StatusTag status={ind.status} />
                  </>
                ) : null}
              </span>
            </div>
          ))}
          {data.indicators.length > 6 && (
            <button
              onClick={() => setExpanded(!expanded)}
              style={{
                border: 'none', background: 'none', color: 'var(--color-primary)',
                fontSize: 12, cursor: 'pointer', padding: '8px 0',
              }}
            >
              {expanded ? '收起' : `展开全部 (${data.indicators.length})`}
            </button>
          )}
        </div>
      )}

      {(data.ai_summary || summaryLoading) && (
        <div style={{
          marginTop: 12, padding: '10px 12px', background: 'var(--color-bg)',
          borderRadius: 'var(--radius-sm)', borderLeft: '3px solid var(--color-primary)',
        }}>
          <div
            onClick={() => setSummaryExpanded(!summaryExpanded)}
            style={{ fontSize: 12, fontWeight: 600, cursor: 'pointer', color: 'var(--color-primary)', marginBottom: 4 }}
          >
            AI 健康变化小结 {summaryLoading ? <Spin size="small" /> : (data.ai_summary_cached ? '(已缓存)' : '(新生成)')} {summaryExpanded ? '▾' : '▸'}
          </div>
          {summaryExpanded && (
            <div style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--color-text)' }}>
              {summaryLoading ? '生成中...' : (data.ai_summary || 'AI 小结暂不可用,查看上方指标对比详情')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 验证构建**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && npm run build -w @hospital/user-portal
```
Expected:无 TS 错误,构建通过(没有未使用 import)。

- [ ] **Step 3: 提交**

```bash
git add frontend/packages/user-portal/src/components/ComparisonCard.tsx
git commit -m "feat(user-portal): add ComparisonCard with baseline-switch and AI summary"
```

---

### Task 8: 改造 `ReportDetailPage.tsx` —— 挂载 ComparisonCard

**Files:**
- Modify: `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`(在指标列表区与 InterpretationReportCard 之间插入)

**Interfaces:**
- Consumes:`ComparisonCard`(Task 7)
- 仅在 `interpretation?.status === 'completed'` 且非用户首份报告时显示对比卡

- [ ] **Step 1: 在 ReportDetailPage.tsx 引入组件**

文件顶部 import 区(在 `import ChatPanel from './components/ChatPanel';` 之后)加:

```tsx
import ComparisonCard from '../components/ComparisonCard';
```

- [ ] **Step 2: 在 `InterpretationReportCard` 上方插入对比卡**

定位 `ReportDetailPage.tsx` 中指标列表区与 `InterpretationReportCard` 中间。找到:

```tsx
      <InterpretationReportCard
        summaries={interpretation?.summaries}
        references={interpretation?.references}
        loading={interpLoading}
        qualityNote={interpretation?.quality_note}
      />
```

在其前面插入:

```tsx
      {interpretation?.status === 'completed' && (
        <ComparisonCard reportId={Number(id)} />
      )}

```

(这样对比卡仅在解读完成后挂载,会自己调 `/profile/compare` 拿数据。)

- [ ] **Step 3: 验证构建**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && npm run build -w @hospital/user-portal
```
Expected:TS 通过,vite build 成功。

- [ ] **Step 4: 提交**

```bash
git add frontend/packages/user-portal/src/pages/ReportDetailPage.tsx
git commit -m "feat(user-portal): mount ComparisonCard under ReportDetailPage when interpretation completed"
```

---

### Task 9: 重写 `ProfilePage.tsx` —— 健康档案页

**Files:**
- Modify: `frontend/packages/user-portal/src/pages/ProfilePage.tsx`(整体重写)

**Interfaces:**
- Consumes:`IndicatorTrendChart`(Task 6)、`ColorBadge`(已有)、`useUserStore`(已有)、antd
- 调 `GET /profile/overview` 取数据

- [ ] **Step 1: 备份并重写 ProfilePage.tsx**

```bash
cp frontend/packages/user-portal/src/pages/ProfilePage.tsx /tmp/opencode/ProfilePage.tsx.bak
```

新内容:

```tsx
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Spin, Input } from 'antd';
import { UserOutlined, LogoutOutlined, SettingOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import IndicatorTrendChart from '../components/IndicatorTrendChart';

interface UserSummary {
  total_reports: number;
  earliest_date: string | null;
  latest_date: string | null;
  latest_overall_level: string | null;
  latest_red: number; latest_yellow: number; latest_green: number;
  baseline_date: string | null;
}

interface TrendPoint {
  report_id: number; report_date: string; value: number; color?: string;
}

interface IndicatorTrend {
  item_name_standard: string | null;
  item_name: string;
  unit: string | null;
  points: TrendPoint[];
  latest_deviation: string | null;
  trend_direction: string | null;
}

interface AbnormalDist {
  item_name_standard: string;
  red_count: number;
  yellow_count: number;
  last_color: string;
}

interface OverviewResponse {
  user_summary: UserSummary | null;
  indicator_trends: IndicatorTrend[];
  abnormal_distribution: AbnormalDist[];
}

export default function ProfilePage() {
  const { api, logout } = useUserStore();
  const nav = useNavigate();
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  useEffect(() => {
    api.get('/profile/overview').then(r => setData(r.data)).catch(() => setData(null)).finally(() => setLoading(false));
  }, []);

  if (loading) return (
    <Layout title="我的健康档案">
      <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" /></div>
    </Layout>
  );

  if (!data || !data.user_summary || data.user_summary.total_reports === 0) {
    return (
      <Layout title="我的健康档案">
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--color-text-secondary)' }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>📋</div>
          <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 8 }}>暂无档案数据</div>
          <div style={{ fontSize: 13 }}>上传您的体检报告后,可在此查看健康变化趋势</div>
        </div>
        <BottomSettings onLogout={() => { logout(); nav('/login'); }} />
      </Layout>
    );
  }

  const s = data.user_summary;
  const filtered = data.indicator_trends.filter(t => {
    if (!search) return true;
    return (t.item_name_standard || t.item_name || '').toLowerCase().includes(search.toLowerCase());
  });
  const topTrends = filtered.slice(0, 10);

  return (
    <Layout title="我的健康档案">
      <div style={{
        background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
        padding: 20, boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <div style={{ width: 48, height: 48, borderRadius: '50%', background: 'var(--color-primary-light)',
            display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <UserOutlined style={{ fontSize: 20, color: 'var(--color-primary)' }} />
          </div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 15 }}>体检用户</div>
            <div style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
              共 {s.total_reports} 份报告 · {s.earliest_date || '未知'} 至 {s.latest_date || '未知'}
            </div>
          </div>
          {s.latest_overall_level && (
            <div style={{ marginLeft: 'auto' }}>
              <ColorBadge level={s.latest_overall_level} />
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, fontSize: 12 }}>
          <span style={{ color: 'var(--color-red)', fontWeight: 600 }}>红区 {s.latest_red}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-yellow)', fontWeight: 600 }}>黄区 {s.latest_yellow}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-green)', fontWeight: 600 }}>绿区 {s.latest_green}</span>
        </div>
      </div>

      {data.abnormal_distribution.length > 0 && (
        <div style={{
          background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
          padding: '16px 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 16,
        }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>异常指标分布</div>
          {data.abnormal_distribution.map((a, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '8px 0', borderBottom: i < data.abnormal_distribution.length - 1 ? '1px solid var(--color-border-light)' : 'none',
              fontSize: 13,
            }}>
              <span>{a.item_name_standard}</span>
              <span style={{ color: 'var(--color-text-secondary)', fontSize: 12 }}>
                红 {a.red_count} · 黄 {a.yellow_count}
                {' '}<ColorBadge level={a.last_color} size="sm" />
              </span>
            </div>
          ))}
        </div>
      )}

      <div style={{
        background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
        padding: '16px 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
      }}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>指标走势</div>
        <Input.Search
          placeholder="搜索指标名"
          size="small" value={search} onChange={e => setSearch(e.target.value)}
          style={{ marginBottom: 12 }}
        />
        {topTrends.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 24, color: 'var(--color-text-secondary)', fontSize: 13 }}>
            暂无可视化指标
          </div>
        ) : (
          topTrends.map((t, i) => {
            const last = t.points[t.points.length - 1];
            return (
              <div key={i} style={{
                padding: '10px 0', borderBottom: i !== topTrends.length - 1 ? '1px solid var(--color-border-light)' : 'none',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                  <span style={{ fontSize: 13, fontWeight: 500 }}>
                    {t.item_name_standard || t.item_name}
                    {t.trend_direction && (
                      <span style={{
                        marginLeft: 6, fontSize: 11,
                        color: t.trend_direction === 'up' ? 'var(--color-red)' : 'var(--color-green)',
                      }}>
                        {t.trend_direction === 'up' ? '↑' : '↓'}
                      </span>
                    )}
                  </span>
                  <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                    {last ? `${last.value}${t.unit ? ' ' + t.unit : ''}` : '-'}
                    {last?.color && <ColorBadge level={last.color} size="sm" />}
                  </span>
                </div>
                <IndicatorTrendChart data={t.points} />
              </div>
            );
          })
        )}
      </div>

      <BottomSettings onLogout={() => { logout(); nav('/login'); }} />
    </Layout>
  );
}

function BottomSettings({ onLogout }: { onLogout: () => void }) {
  return (
    <div style={{
      marginTop: 24, background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
      overflow: 'hidden', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)',
    }}>
      <div
        onClick={() => {}}
        style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
          cursor: 'pointer', borderBottom: '1px solid var(--color-border-light)',
        }}
      >
        <SettingOutlined />
        <span style={{ fontSize: 14 }}>设置</span>
        <span style={{ marginLeft: 'auto', color: 'var(--color-text-secondary)' }}>›</span>
      </div>
      <div
        onClick={onLogout}
        style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
          cursor: 'pointer', color: 'var(--color-red)',
        }}
      >
        <LogoutOutlined />
        <span style={{ fontSize: 14 }}>退出登录</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 验证构建**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && npm run build -w @hospital/user-portal
```
Expected:TS 与 vite build 通过(无未使用 import)。

- [ ] **Step 3: 提交**

```bash
git add frontend/packages/user-portal/src/pages/ProfilePage.tsx
git commit -m "feat(user-portal): rewrite ProfilePage as health archive (overview + abnormal + trends)"
```

---

### Task 10: 端到端冒烟验证(手测脚本)

**Files:** 无,只验证整套链路

- [ ] **Step 1: 起全栈**

```bash
cd /data/project/hospitalKnowledgeBase && bash start.sh
```
Expected:5 个端口在 90 秒内 UP,通过 `for p in 8000 8004 8002 8003 8001; do curl -s -m2 http://localhost:$p/health >/dev/null && echo ":$p UP" || echo ":$p DOWN"; done` 验证。

- [ ] **Step 2: 起前端 dev server**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && npm run dev -w @hospital/user-portal
```
Expected:vite dev 在 3001 端口启动。

- [ ] **Step 3: 手测档案页**

浏览器开 `http://localhost:3001/profile`(需先登录)。

验证:
- 顶部摘要卡显示总报告数、跨度、最新总体级别 + 红/黄/绿计数条
- 异常指标分布区按 red_count desc 排序
- 指标走势区默认展示 top N,每个 item 显示 `IndicatorTrendChart`(≥2 个点才渲染)
- 搜索框过滤指标
- 底部"设置 / 退出登录"两个入口

- [ ] **Step 4: 手测上传对比卡**

选一份用户已有 ≥2 份报告的账号,进入最新报告详情页 `/report/<id>`。

验证:
- `📊 与上次报告对比` 卡出现在指标列表与 AI 解读报告卡之间
- 顶部 Select 显示基准报告(默认自动选最近的历史报告)
- 总体变化条:`红区 5→3 (↓2)` 之类
- 指标差异表显示至少一项指标(若两份报告有共同标准化指标)
- AI 小结段展示 worker 钩子写入的 `comparison_summary`(标注 "(已缓存)")
- 切换基准 Select → 小结段显示 `<Spin />` → 显示新生成小结,标注 "(新生成)"

- [ ] **Step 5: 手测新报告上传的全链路**

在另一用户(或测试用户)下:
1. 上传第一份报告 (1 周前的 PDF) → 等解读完成
2. 上传第二份报告(最新 PDF) → 等 `interpretation` worker 完成
3. 进入第二份报告详情页,验证对比卡显示(基准自动为第一份)
4. 仅查看 ProfilePage,验证两份报告都有趋势点

- [ ] **Step 6: 后端最终全量测试**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/user_profile tests/ai/agents -v
```
Expected:17 passed(本特性),ai/agents 原有测试无回归。

- [ ] **Step 7: 提交(若有微调)**

如果手测中发现小问题并修复,提交:

```bash
git add -A
git commit -m "fix(user-profile): smoke-test fixes from end-to-end verification"
```

否则跳过这一步。

---

## Self-Review 检查表(供实现者对照 spec)

完成前再扫一遍:

- [ ] spec 第 2 节 3 个接口都已实现并挂在 `/api/v1/profile`
- [ ] spec 第 3 节 AI 小结在 worker 钩子中生成、缓存命中跳过、缓存 mismatch 时 `/ai-summary` 实时生成不写回
- [ ] spec 第 4 节 DDL 已在两库执行,ORM 字段已加
- [ ] spec 第 5 节 前端 3 个新组件 + 2 个改造页面已落地
- [ ] spec 第 6 节 7 个错误场景都有对应处理(无基准、非数值、LLM 失败、baseline mismatch 等)
- [ ] `comparison.py` 与 `service.py` 各自单测全绿
- [ ] `npm run build -w @hospital/user-portal` 通过