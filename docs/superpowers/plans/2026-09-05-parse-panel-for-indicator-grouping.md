# 解析落库栏目(panel)修复同名误归组 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让新建文本型 PDF 报告在解析时由 LLM 给每条指标标出所属栏目并落库 `report_indicator.category`,读侧分组优先用落库栏目、名字分类兜底,修掉裸名(`葡萄糖`/`白细胞`…)尿样/血样误归组。

**Architecture:** 三处后端改动,前端零改动。
1. `backend/app/core/indicator_groups.py`:`PANEL_HINTS`(合法栏目 = 有分类规则的模块,Excel 序)+ `normalize_panel()`(去空白校验)+ `group_indicators()` 优先读 `row["category"]`。
2. `backend/app/modules/report/service.py`:文本 PDF 解析 prompt 让 LLM 输出 `category`(只能从允许栏目选,不能自创,不确定填 null),`process_task` 落库时 `category=normalize_panel(ind.get("category"))`。
3. `backend/app/modules/interpretation/service.py`:`get_judgments_with_indicator_detail` 的 SELECT/返回 dict 带 `i.category`。

**Tech Stack:** Python 3.10 / FastAPI / sqlalchemy;pytest 用 backend venv。

## Global Constraints
- 后端测试一律 `cd backend && .venv/bin/python -m pytest <path> -q`。
- **扫描/图片 PDF、存量报告不改**:category 缺失时行为与现状一致(名字分类兜底)。
- 无 schema/DDL/迁移(`report_indicator.category` 列已存在,现为 NULL);响应结构不变。
- 信任落库合法 category(panel 优先),仅当无 category 时用 `classify(item_name)`。
- 只改三处后端文件 + 对应测试;前端/分类规则关键词不动。
- 代码沿用仓库风格(中文注释/commit message),不加 emoji。

---
### Task 1: indicator_groups 栏目校验 + 分组优先级 + 测试

**Files:**
- Modify: `backend/app/core/indicator_groups.py`(在 `_norm` 之后、`classify` 之前插入;并改 `group_indicators`)
- Test: `backend/tests/core/test_indicator_panel.py`

**Interfaces:**
- Produces: `normalize_panel(raw: Optional[str]) -> Optional[str]`、`PANEL_HINTS: Tuple[str, ...]`;Task2 用 `PANEL_HINTS`+`normalize_panel`,Task3 依赖 `group_indicators` 的 category 优先语义。

- [ ] **Step 1: 写失败测试** `backend/tests/core/test_indicator_panel.py`

```python
from app.core.indicator_groups import normalize_panel, group_indicators


def test_normalize_panel_valid_and_whitespace():
    assert normalize_panel("尿常规") == "尿常规"
    assert normalize_panel("  尿常规 ") == "尿常规"
    assert normalize_panel("尿 常规") == "尿常规"   # 内部空格(含全角空格场景)
    assert normalize_panel(None) is None
    assert normalize_panel("") is None
    assert normalize_panel("   ") is None
    assert normalize_panel("乱写") is None
    assert normalize_panel("血常规,糖化血红蛋白") is None  # 非白名单整串不进


def test_group_stored_category_wins_over_name():
    rows, order = group_indicators([
        {"item_name": "葡萄糖", "category": "尿常规"},
        {"item_name": "白细胞", "category": "尿常规"},
        {"item_name": "全血糖化血红蛋白测定", "category": "糖化血红蛋白"},
    ])
    by = {r["item_name"]: r["group"] for r in rows}
    assert by["葡萄糖"] == "尿常规"                 # 名字会归 空腹血糖
    assert by["白细胞"] == "尿常规"                 # 名字会归 血常规
    assert by["全血糖化血红蛋白测定"] == "糖化血红蛋白"  # 名字会归 糖化血红蛋白(但保真)
    assert order == ["尿常规", "糖化血红蛋白"]


def test_group_invalid_category_falls_back_to_name():
    rows, _ = group_indicators([
        {"item_name": "葡萄糖", "category": "乱写"},
        {"item_name": "葡萄糖", "category": "   "},
        {"item_name": "白细胞", "category": "NMP22测定"},
    ])
    assert [r["group"] for r in rows] == ["空腹血糖", "空腹血糖", "血常规"]


def test_group_without_category_unchanged():
    rows, _ = group_indicators([{"item_name": "血红蛋白"}, {"item_name": "尿酸碱度"}])
    assert [r["group"] for r in rows] == ["血常规", "尿常规"]
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_indicator_panel.py -q`
Expected: FAIL(`ImportError: cannot import name 'normalize_panel'`)

- [ ] **Step 3: 实现 `indicator_groups.py`**

在 `_norm` 函数之后、`classify` 之前插入:

```python
# 可作落库/分组栏目的合法取值 = 能折叠的模块(有分类规则的子集),按 Excel 顺序
PANEL_HINTS: Tuple[str, ...] = tuple(m for m in MODULE_ORDER if m in _RULES)


def normalize_panel(raw: Optional[str]) -> Optional[str]:
    """清洗模型输出的栏目:去空白;命中 PANEL_HINTS 才返回,否则 None。"""
    if not raw:
        return None
    v = _norm(str(raw))
    return v if v in PANEL_HINTS else None
```

`group_indicators` 内循环改为(category 优先,名字兜底):

```python
    for row in rows:
        stored = normalize_panel(row.get("category") or "")
        g = stored if stored else classify(str(row.get("item_name") or ""))
        row["group"] = g
        if g and g not in present:
            present.append(g)
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/core/test_indicator_panel.py tests/core/test_indicator_groups.py -q`
Expected: PASS(新旧全绿)

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/indicator_groups.py backend/tests/core/test_indicator_panel.py
git commit -m "feat: 分组优先用落库栏目 category,名字分类兜底"
```

---
### Task 2: 文本解析 prompt 输出 category + 落库 + 测试

**Files:**
- Modify: `backend/app/modules/report/service.py`(`_build_parse_prompt`、`process_task` 落库循环)
- Test: `backend/tests/test_parse_category_persist.py`

**Interfaces:**
- Consumes: `indicator_groups.PANEL_HINTS`、`indicator_groups.normalize_panel`(Task1)
- Produces: 新建文本 PDF 的 `report_indicator.category` ∈ PANEL_HINTS(合法)或 NULL

- [ ] **Step 1: 写失败测试** `backend/tests/test_parse_category_persist.py`

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_parse_category_persist.py -q`
Expected: FAIL(process_task 未落 category 等,断言失败)

- [ ] **Step 3: 实现 prompt 与落库**

3a. 用下方完整实现替换 `_build_parse_prompt`(`report/service.py`):

```python
def _build_parse_prompt(text: str) -> str:
    from app.core.indicator_groups import PANEL_HINTS
    allowed = "\n   - ".join(["", *PANEL_HINTS])
    return f"""从以下体检报告文本中提取信息，返回 JSON 格式（不要 Markdown 代码块）：

{{
  "name": "姓名",
  "gender": "男或女",
  "age": 年龄数字或null,
  "report_date": "YYYY-MM-DD或null",
  "indicators": [
    {{"item_name": "指标名称", "result": "检测结果", "unit": "单位", "ref_low": "参考下限", "ref_high": "参考上限", "category": "所属栏目"}}
  ]
}}

规则：
1. 姓名从"尊敬的XXX先生/女士"或"姓名:XXX"提取
2. 性别："先生"→男，"女士"→女
3. 年龄：从"XX岁"提取数字
4. 参考范围如"3.5-9.5"→ref_low="3.5", ref_high="9.5"；如"<5.0"→ref_low="", ref_high="5.0"
5. 只提取化验指标数据（血常规、生化、免疫等），不提取问卷、个人信息
6. 每条指标必须给 category：该指标所属栏目，只能取下面列出的取值中最接近的一项，不能自创、不能附加说明文字：
{allowed}
   表格上方的栏目标题通常已给出栏目，如"尿常规"、"血常规（体检）,糖化血红蛋白"（一个标题含多个栏目时按各指标归属拆标：血常规行→血常规，全血糖化血红蛋白测定→糖化血红蛋白）；找不到任何合适栏目时 category 填 null
7. 没有的字段填 null

体检报告文本：
{text[:24000]}
"""
```

3b. `process_task` 落库循环(`for ind in indicators:` 前加 import,构造加 category):

```python
        from app.core.indicator_groups import normalize_panel
        for ind in indicators:
            db.add(ReportIndicator(
                report_id=report.id,
                item_name=ind.get("item_name", ""),
                item_name_standard=ind.get("item_name_standard"),
                item_code=ind.get("item_code"),
                result_value=ind.get("result"),
                unit=ind.get("unit"),
                ref_range_low=ind.get("ref_low"),
                ref_range_high=ind.get("ref_high"),
                category=normalize_panel(ind.get("category")),
                raw_text=ind.get("raw_text"),
            ))
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_parse_category_persist.py tests/test_report_parsed_name.py -q`
Expected: PASS(含既有回归)

- [ ] **Step 5: 提交**

```bash
git add backend/app/modules/report/service.py backend/tests/test_parse_category_persist.py
git commit -m "feat: 文本解析给指标输出 category 并落库"
```

---
### Task 3: 解读 join 带 i.category + DB 测试

**Files:**
- Modify: `backend/app/modules/interpretation/service.py`(`get_judgments_with_indicator_detail`)
- Test: `backend/tests/test_interp_join_category.py`

**Interfaces:**
- Consumes: `group_indicators` 的 category 优先(展示端已有,无需再改 router)
- Produces: `GET /interpretations/{id}` 的行 dict 带 `category`(`None` 保留)

- [ ] **Step 1: 写失败测试** `backend/tests/test_interp_join_category.py`

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/python -m pytest tests/test_interp_join_category.py -q`
Expected: FAIL(返回 dict 无 category)

- [ ] **Step 3: 实现 `get_judgments_with_indicator_detail`**

把 SELECT 末尾 `i.ref_range_high` 后加 `, i.category`,返回 dict 加键:

```python
def get_judgments_with_indicator_detail(db: Session, interpretation_id: int) -> list[dict]:
    """join indicator_judgment 与 report_indicator，返回前端展示所需字段

    含 deviation/color_level（来自 judgment）+ unit/ref_range_low/ref_range_high/category（来自 indicator）
    """
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT j.indicator_id, j.item_name, j.result_value, j.deviation, j.color_level, "
        "i.unit, i.ref_range_low, i.ref_range_high, i.category "
        "FROM indicator_judgment j "
        "LEFT JOIN report_indicator i ON i.id = j.indicator_id "
        "WHERE j.interpretation_id = :iid ORDER BY j.id"
    ), {"iid": interpretation_id}).fetchall()
    return [
        {"indicator_id": r[0], "item_name": r[1], "result_value": r[2],
         "deviation": r[3], "color_level": r[4], "unit": r[5],
         "ref_range_low": r[6], "ref_range_high": r[7], "category": r[8]}
        for r in rows
    ]
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/python -m pytest tests/test_interp_join_category.py tests/test_interp_detail_grouping.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/modules/interpretation/service.py backend/tests/test_interp_join_category.py
git commit -m "feat: 解读指标 join 带 report_indicator.category"
```

---
### 收尾验证
后端全量相关回归(改动后再跑):
`cd backend && .venv/bin/python -m pytest tests/core/test_indicator_groups.py tests/core/test_indicator_panel.py tests/core/test_excel_module_order.py tests/test_report_detail_grouping.py tests/test_interp_detail_grouping.py tests/test_report_parsed_name.py tests/test_parse_category_persist.py tests/test_interp_join_category.py -q`
Expected: 全绿

手工验收(需重启后端后,新建一份文本 PDF 上传):
- 尿试纸 `葡萄糖=阴性` 在解读详情 `category=尿常规`,折叠进尿常规;
- CBC `白细胞` category=血常规;生化 `葡萄糖=5.7` category=空腹血糖;
- 存量/扫描件分组与改动前一致。
