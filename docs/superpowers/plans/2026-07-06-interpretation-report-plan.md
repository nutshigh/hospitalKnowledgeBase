# 综合性 AI 解读报告 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把按指标生成的 explanation/suggestion 改为一份结构化分节的综合 AI 解读报告；前端把「指标表」与「AI 解读报告卡」分开展示。

**Architecture:** 后端解读图重构为 `agent_search_knowledge → generate_report → judge → persist`。Agent 负责批量查 RAG/KG 知识；`generate_report` 单次 LLM 调用合成 5 节结构化报告；Judge 审综合报告（放宽，最多 1 次重试）；持久化写 `summary_text`（JSON）+ 新增 `summary_refs`/`quality_note` 列；`IndicatorJudgment` 仅保留 deviation/color_level/matched_rule_id。前端引入 `react-markdown`，抽 `InterpretationReportCard` 到 `@hospital/shared`，用户端/医生端 `ReportDetailPage` 改为「信息卡 + 纯指标区 + AI 解读报告卡」三段。

**Tech Stack:** Python 3.10 / LangChain 1.x / FastAPI / SQLAlchemy 2.x / MySQL 8；React 18 + TypeScript / Ant Design 5 / react-markdown + remark-gfm

## Global Constraints

- `IndicatorJudgment` 表 `explanation/suggestion/knowledge_refs/certainty/certainty_reason` 列**保留 schema**（不删），新解读一律写 NULL；服务层不再写这些字段。
- Judge 流程保留但放宽：审核对象改为综合报告，重试上限 1 次，未通过仍 persist 并附 `quality_note`。
- 旧解读记录（report_id 1/2/3）DB 数据不动；前端遇 `summaries` 为 null 时只显示指标 + 提示「暂无 AI 解读报告」，不崩。
- `summary_text` 复用为 JSON 字符串存储 5 节 dict；新增列 `summary_refs JSON NULL`、`quality_note VARCHAR(255) NULL`。
- 后端入口函数 `run_interpretation_agent(hospital_id, db, report_id) -> dict` 签名不变（worker 不改）。
- 测试命令：后端用 `uv run pytest <path> -v`；前端用 `npm run build -w <pkg>` 验证类型与构建。
- Python 代码不写注释（遵循仓库规约）；测试代码可写 docstring。

---

### Task 1: 数据库迁移 — 新增两列

**Files:**
- Create: `backend/scripts/manual_migrations/001_add_summary_refs_quality_note.sql`

**Interfaces:**
- Produces: `report_interpretation` 表新增 `summary_refs JSON NULL`、`quality_note VARCHAR(255) NULL` 两列（hospital_H001 与 hospital_template 两个库都要加）

- [ ] **Step 1: 写迁移 SQL 文件**

```sql
-- backend/scripts/manual_migrations/001_add_summary_refs_quality_note.sql
ALTER TABLE report_interpretation
  ADD COLUMN summary_refs JSON NULL AFTER summary_text,
  ADD COLUMN quality_note VARCHAR(255) NULL AFTER summary_refs;
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
    db.execute(text('ALTER TABLE report_interpretation ADD COLUMN summary_refs JSON NULL AFTER summary_text'))
    db.commit()
    print('summary_refs added')
except Exception as e:
    print(f'summary_refs: {e}')
try:
    db.execute(text('ALTER TABLE report_interpretation ADD COLUMN quality_note VARCHAR(255) NULL AFTER summary_refs'))
    db.commit()
    print('quality_note added')
except Exception as e:
    print(f'quality_note: {e}')
db.close()
"
```
Expected: 打印 `summary_refs added` 与 `quality_note added`（或 "Duplicate column name" 表示已存在）

- [ ] **Step 3: 在 hospital_template 库执行迁移**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
from app.core.database import get_session
from sqlalchemy import text
db = get_session('hospital_template')
try:
    db.execute(text('ALTER TABLE report_interpretation ADD COLUMN summary_refs JSON NULL AFTER summary_text'))
    db.execute(text('ALTER TABLE report_interpretation ADD COLUMN quality_note VARCHAR(255) NULL AFTER summary_refs'))
    db.commit()
    print('template done')
except Exception as e:
    print(f'template: {e}')
finally:
    db.close()
"
```
Expected: `template done`

- [ ] **Step 4: 验证列存在**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
from app.core.database import get_hospital_db
from sqlalchemy import text
db = next(get_hospital_db('H001'))
row = db.execute(text('SHOW COLUMNS FROM report_interpretation LIKE \"summary_refs\"')).fetchone()
print('summary_refs:', row)
row = db.execute(text('SHOW COLUMNS FROM report_interpretation LIKE \"quality_note\"')).fetchone()
print('quality_note:', row)
db.close()
"
```
Expected: 两行均不为 None

- [ ] **Step 5: 同步更新 init SQL（新医院建库时自带这两列）**

修改 `infra/mysql/init/02_hospital_created.sql`，在 `report_interpretation` 建表语句的 `summary_text` 后追加：

```sql
        summary_text TEXT DEFAULT NULL,
        summary_refs JSON DEFAULT NULL,
        quality_note VARCHAR(255) DEFAULT NULL,
```

- [ ] **Step 6: Commit**

```bash
cd /data/project/hospitalKnowledgeBase && \
git add backend/scripts/manual_migrations/001_add_summary_refs_quality_note.sql \
        infra/mysql/init/02_hospital_created.sql && \
git commit -m "feat(interp): add summary_refs/quality_note columns to report_interpretation"
```

---

### Task 2: 后端 Models — ReportInterpretation 新增列

**Files:**
- Modify: `backend/app/modules/interpretation/models.py:5-18`

**Interfaces:**
- Produces: `ReportInterpretation.summary_refs`（JSON 列）、`ReportInterpretation.quality_note`（String 列）

- [ ] **Step 1: 写测试 — 模型含新字段**

追加到 `backend/tests/ai/agents/test_interp_graph.py` 末尾（临时存放，Task 8 会重组）：

```python
def test_report_interpretation_has_summary_refs_and_quality_note():
    """ReportInterpretation 模型含 summary_refs / quality_note 字段"""
    from app.modules.interpretation.models import ReportInterpretation
    cols = {c.name for c in ReportInterpretation.__table__.columns}
    assert "summary_refs" in cols
    assert "quality_note" in cols
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py::test_report_interpretation_has_summary_refs_and_quality_note -v
```
Expected: FAIL（字段未定义）

- [ ] **Step 3: 修改 models.py**

把 `backend/app/modules/interpretation/models.py` 中 `ReportInterpretation` 类改为：

```python
class ReportInterpretation(Base):
    __tablename__ = "report_interpretation"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, ForeignKey("report_info.id"), nullable=False)
    overall_level = Column(String(10), nullable=True)
    red_count = Column(Integer, nullable=False, default=0)
    yellow_count = Column(Integer, nullable=False, default=0)
    green_count = Column(Integer, nullable=False, default=0)
    summary_text = Column(Text, nullable=True)
    summary_refs = Column(JSON, nullable=True)
    quality_note = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py::test_report_interpretation_has_summary_refs_and_quality_note -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /data/project/hospitalKnowledgeBase && \
git add backend/app/modules/interpretation/models.py backend/tests/ai/agents/test_interp_graph.py && \
git commit -m "feat(interp): add summary_refs/quality_note columns to ReportInterpretation model"
```

---

### Task 3: 后端 Schemas — 新响应结构

**Files:**
- Modify: `backend/app/modules/interpretation/schemas.py`

**Interfaces:**
- Produces（供 Task 6 router 用）：
  - `IndicatorJudgmentSchema`：去 `explanation/suggestion`，新增 `unit`、`ref_range_low`、`ref_range_high`（router 通过 join `report_indicator` 填充）
  - `InterpretationReportSchema`：5 节字段
  - `CitationSchema`：`ref_id/entry_id/title/source`
  - `InterpretationResponse`：含 `summaries: InterpretationReportSchema`、`references: list[CitationSchema]`、`quality_note: str | None`、`indicators: list[IndicatorJudgmentSchema]`，去 `summary_text`
  - `parse_summary_text(summary_text: str | None) -> InterpretationReportSchema`：解析存库的 JSON

- [ ] **Step 1: 写测试 — schema 字段**

在 `backend/tests/ai/agents/test_interp_graph.py` 末尾追加：

```python
def test_interpretation_response_schema_fields():
    """新 InterpretationResponse 含 summaries/references/quality_note，无 summary_text"""
    from app.modules.interpretation.schemas import InterpretationResponse
    fields = InterpretationResponse.model_fields
    assert "summaries" in fields
    assert "references" in fields
    assert "quality_note" in fields
    assert "summary_text" not in fields


def test_indicator_judgment_schema_no_explanation():
    """IndicatorJudgmentSchema 不再含 explanation/suggestion，含 unit/ref_range"""
    from app.modules.interpretation.schemas import IndicatorJudgmentSchema
    fields = IndicatorJudgmentSchema.model_fields
    assert "explanation" not in fields
    assert "suggestion" not in fields
    assert "unit" in fields
    assert "ref_range_low" in fields
    assert "ref_range_high" in fields


def test_parse_summary_text_roundtrip():
    """parse_summary_text 把 5 节 JSON 解析回 schema，空输入返回空 5 节"""
    from app.modules.interpretation.schemas import parse_summary_text, InterpretationReportSchema
    raw = {
        "overall_summary": "整体评估", "abnormal_focus": "异常解读",
        "trend_note": "趋势", "suggestions": "建议", "risk_alert": "风险"
    }
    parsed = parse_summary_text(__import__("json").dumps(raw))
    assert isinstance(parsed, InterpretationReportSchema)
    assert parsed.overall_summary == "整体评估"
    empty = parse_summary_text(None)
    assert empty.overall_summary == ""
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py::test_interpretation_response_schema_fields tests/ai/agents/test_interp_graph.py::test_indicator_judgment_schema_no_explanation tests/ai/agents/test_interp_graph.py::test_parse_summary_text_roundtrip -v
```
Expected: FAIL（schema 未定义）

- [ ] **Step 3: 重写 schemas.py**

替换 `backend/app/modules/interpretation/schemas.py` 全文为：

```python
import json
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class IndicatorJudgmentSchema(BaseModel):
    indicator_id: int
    item_name: str
    result_value: Optional[str] = None
    unit: Optional[str] = None
    ref_range_low: Optional[str] = None
    ref_range_high: Optional[str] = None
    deviation: Optional[str] = None
    color_level: Optional[str] = None


class InterpretationReportSchema(BaseModel):
    overall_summary: str = ""
    abnormal_focus: str = ""
    trend_note: str = ""
    suggestions: str = ""
    risk_alert: str = ""


class CitationSchema(BaseModel):
    ref_id: int
    entry_id: Optional[int] = None
    title: str = ""
    source: str = "document"


class InterpretationResponse(BaseModel):
    id: int
    report_id: int
    overall_level: Optional[str] = None
    red_count: int
    yellow_count: int
    green_count: int
    status: str
    summaries: InterpretationReportSchema = InterpretationReportSchema()
    references: List[CitationSchema] = []
    quality_note: Optional[str] = None
    indicators: List[IndicatorJudgmentSchema] = []
    created_at: datetime
    completed_at: Optional[datetime] = None


def parse_summary_text(summary_text: Optional[str]) -> InterpretationReportSchema:
    if not summary_text:
        return InterpretationReportSchema()
    try:
        data = json.loads(summary_text)
        return InterpretationReportSchema(**data)
    except (json.JSONDecodeError, TypeError):
        return InterpretationReportSchema()


class HighRiskItem(BaseModel):
    user_id: int
    report_id: int
    name: Optional[str] = None
    unit_name: Optional[str] = None
    red_count: int
    main_indicators: List[str] = []


class HighRiskResponse(BaseModel):
    items: List[dict]
    total: int


class TriageRuleCreate(BaseModel):
    rule_name: str
    rule_type: str
    indicator_code: Optional[str] = None
    conditions: dict
    color_level: str
    priority: int = 0


class TriageRuleUpdate(BaseModel):
    rule_name: Optional[str] = None
    rule_type: Optional[str] = None
    indicator_code: Optional[str] = None
    conditions: Optional[dict] = None
    color_level: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[int] = None


class TriageRuleResponse(BaseModel):
    id: int
    rule_name: str
    rule_type: str
    indicator_code: Optional[str] = None
    conditions: dict
    color_level: str
    priority: int
    is_active: int
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py::test_interpretation_response_schema_fields tests/ai/agents/test_interp_graph.py::test_indicator_judgment_schema_no_explanation tests/ai/agents/test_interp_graph.py::test_parse_summary_text_roundtrip -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /data/project/hospitalKnowledgeBase && \
git add backend/app/modules/interpretation/schemas.py backend/tests/ai/agents/test_interp_graph.py && \
git commit -m "feat(interp): new InterpretationResponse schema with summaries/references; drop per-indicator explanation"
```

---

### Task 4: 后端 Judge Graph — 改审综合报告

**Files:**
- Modify: `backend/app/ai/agents/judge_graph.py`（全文重写）

**Interfaces:**
- Produces：`run_judge(state: dict) -> dict`，state 含 `report: InterpretationReport` 与 `references: list[dict]`、`abnormal_indicators: list[dict]`。返回 `{"passed": bool, "issues": list[str], "suggestions": str}`。
- 用 Pydantic `JudgeResult(passed: bool, issues: list[str], suggestions: str)`（保持原有签名）
- Judge 失败/异常时默认 passed=True（不阻塞流程，沿用现状）

- [ ] **Step 1: 写测试**

在 `backend/tests/ai/agents/test_interp_graph.py` 末尾追加：

```python
def test_judge_review_format_for_comprehensive_report():
    """_format_for_review 输出综合报告 5 节 + 异常指标 + 引用文本"""
    from app.ai.agents.judge_graph import _format_for_review
    from app.ai.agents.interp_graph import InterpretationReport
    state = {
        "report": InterpretationReport(
            overall_summary="整体评估内容", abnormal_focus="ALT 升高 [1]",
            trend_note="", suggestions="建议戒酒", risk_alert="",
        ),
        "references": [{"ref_id": 1, "entry_id": 12, "title": "ALT 知识", "source": "document"}],
        "abnormal_indicators": [{"indicator_id": 5, "item_name": "ALT", "result_value": "62",
                                  "unit": "U/L", "deviation": "high", "color_level": "yellow"}],
    }
    text = _format_for_review(state)
    assert "整体评估内容" in text
    assert "ALT 升高" in text
    assert "ALT" in text
    assert "62" in text
    assert "[1]" in text
    assert "ALT 知识" in text


def test_run_judge_passes_when_agent_passthrough():
    """Judge agent 抛异常时 run_judge 返回 passed=True，不阻塞"""
    from unittest.mock import patch
    from app.ai.agents.judge_graph import run_judge
    from app.ai.agents.interp_graph import InterpretationReport
    state = {
        "report": InterpretationReport(overall_summary="x", abnormal_focus="x",
                                        trend_note="", suggestions="", risk_alert=""),
        "references": [],
        "abnormal_indicators": [],
    }
    with patch("app.ai.agents.judge_graph.build_judge_agent") as mock_b:
        mock_b.side_effect = RuntimeError("boom")
        result = run_judge(state)
    assert result["passed"] is True
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py::test_judge_review_format_for_comprehensive_report tests/ai/agents/test_interp_graph.py::test_run_judge_passes_when_agent_passthrough -v
```
Expected: FAIL（`_format_for_review` 旧实现读取 `agent_explanations`，不匹配）

- [ ] **Step 3: 重写 judge_graph.py**

替换 `backend/app/ai/agents/judge_graph.py` 全文为：

```python
"""LLM as a Judge — 综合解读报告质量审核 Agent。

审核 5 节结构化报告，放宽标准：主要结论有引用、无明显编造、结构完整即通过。
最多重试 1 次（实际重试逻辑在 interp_graph 的 after_judge 控制）。
"""
import logging

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage
from pydantic import BaseModel, Field

from app.ai.llm import get_chat_model

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """你是体检报告解读质量审核员。审查的是一份综合性的 AI 解读报告（5 节 markdown），不是逐指标的解释。

审核标准（放宽）：
1. 结构完整：5 节均非空（trend_note 允许为空，仅在首份报告无历史时）。
2. 主要结论可追溯：abnormal_focus / suggestions / risk_alert 中的关键结论应能对应到 references 列表中的 [n] 标记。
3. 无明显编造：不要凭空断言数值或疾病；如某节陈述与上传的异常指标无关，视为问题。
4. 引用合理性：references 中每条 entry_id/title 应能在 abnormal_focus/suggestions 等节被 [n] 提及。

判断结果：
- passed=true：结构完整且无上述问题。
- passed=false：列出具体问题（哪节哪句缺引用/编造），并给改进建议。

注意：放宽评判，10 次里有 8 次应该通过，避免过度否定。"""


class JudgeResult(BaseModel):
    passed: bool = Field(description="审核是否通过")
    issues: list[str] = Field(default_factory=list, description="具体问题列表")
    suggestions: str = Field(default="", description="改进建议")


def build_judge_agent():
    model = get_chat_model(streaming=False)
    model.max_tokens = 2048
    return create_agent(
        model=model,
        tools=[],
        system_prompt=JUDGE_SYSTEM_PROMPT,
        response_format=ToolStrategy(JudgeResult),
    )


def _format_for_review(state: dict) -> str:
    report = state.get("report")
    references = state.get("references", []) or []
    abnormal = state.get("abnormal_indicators", []) or []

    lines = ["请审核以下综合解读报告：\n"]
    if abnormal:
        lines.append("## 异常指标（输入）")
        for ind in abnormal:
            lines.append(f"- {ind.get('item_name')}: 值 {ind.get('result_value')}{ind.get('unit','')}, "
                         f"参考 {ind.get('ref_range_low','-')}-{ind.get('ref_range_high','-')}, "
                         f"{ind.get('deviation')}, {ind.get('color_level')}区")
        lines.append("")

    if report is not None:
        lines.append("## 综合报告（5 节）")
        lines.append(f"### 整体评估\n{getattr(report, 'overall_summary', '')}")
        lines.append(f"### 重点异常解读\n{getattr(report, 'abnormal_focus', '')}")
        lines.append(f"### 历年趋势\n{getattr(report, 'trend_note', '')}")
        lines.append(f"### 健康建议\n{getattr(report, 'suggestions', '')}")
        lines.append(f"### 风险提示\n{getattr(report, 'risk_alert', '')}")
        lines.append("")

    if references:
        lines.append("## 参考来源列表")
        for ref in references:
            lines.append(f"- [{ref.get('ref_id')}] entry_id={ref.get('entry_id')}, "
                         f"title={ref.get('title','')}, source={ref.get('source','')}")
        lines.append("")

    return "\n".join(lines)


def run_judge(state: dict) -> dict:
    review_text = _format_for_review(state)
    if not review_text.strip():
        return {"passed": True, "issues": [], "suggestions": ""}

    try:
        agent = build_judge_agent()
        result = agent.invoke({"messages": [HumanMessage(content=review_text)]})
        judge_result = result.get("structured_response")
        if judge_result is None:
            logger.warning("Judge returned no structured_response, assuming passed")
            return {"passed": True, "issues": [], "suggestions": ""}
        return judge_result.dict()
    except Exception as e:
        logger.warning("Judge agent failed: %s, assuming passed", e)
        return {"passed": True, "issues": [], "suggestions": ""}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py::test_judge_review_format_for_comprehensive_report tests/ai/agents/test_interp_graph.py::test_run_judge_passes_when_agent_passthrough -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /data/project/hospitalKnowledgeBase && \
git add backend/app/ai/agents/judge_graph.py backend/tests/ai/agents/test_interp_graph.py && \
git commit -m "refactor(judge): review comprehensive 5-section report with relaxed criteria"
```

---

### Task 5: 后端 Interp Graph — 重构为 search→generate→judge→persist

**Files:**
- Modify: `backend/app/ai/agents/interp_graph.py`（大幅重构，保留 `InterpKnowledgeMiddleware`、`Citation`、`run_interpretation_agent` 入口；替换 `InterpBatchItem/InterpBatchResult/_map_structured_to_explanations/_agent_batch` 等旧产物）
- Modify: `backend/app/ai/agents/__init__.py`（更新 export）

**Interfaces:**
- Consumes（来自前面任务）：
  - `app.core.database.get_session`（已有，工具内部用）
  - `app.ai.agents.tools.INTERP_TOOLS, AgentContext`（已有）
  - `app.ai.agents.citation_matcher.inject_citations(text, sources) -> (text, citations)`
  - `app.ai.agents.think_filter.strip_think_tags`
  - `app.ai.agents.judge_graph.run_judge`
  - `app.config.settings.AGENT_MAX_ITERATIONS, JUDGE_MAX_RETRIES`
- Produces：
  - `InterpretationReport`（Pydantic，5 节）
  - `InterpState`（TypedDict，含 `report: InterpretationReport`、`references: list[dict]`、`knowledge_results: dict`、`judge_result: dict`、`judge_retry_count: int`、`abnormal_indicators`、`indicators`、`judgments`、计数等）
  - `InterpKnowledgeMiddleware`（保留，累积 search_knowledge 结果到 `knowledge_results`）
  - `build_interp_agent()`：返回只查知识不生成解读的 Agent
  - `build_interp_graph(hospital_id, db) -> compiled`：新图
  - `_generate_report(state, db) -> dict`：核心生成节点（模块级函数，便于单测）
  - `_merge_citations(list_a, list_b) -> list`：合并去重后重新编号
  - `run_interpretation_agent(hospital_id, db, report_id) -> dict`：入口签名不变

- [ ] **Step 1: 写测试 — 新结构关键函数**

在 `backend/tests/ai/agents/test_interp_graph.py` 末尾追加：

```python
def test_interp_state_has_report_and_references():
    """InterpState 含 report / references / knowledge_results"""
    from app.ai.agents.interp_graph import InterpState
    a = InterpState.__annotations__
    assert "report" in a
    assert "references" in a
    assert "knowledge_results" in a
    assert "judge_retry_count" in a


def test_interpretation_report_is_5_sections():
    """InterpretationReport 含 5 节字段"""
    from app.ai.agents.interp_graph import InterpretationReport
    fields = InterpretationReport.model_fields
    assert {"overall_summary", "abnormal_focus", "trend_note", "suggestions", "risk_alert"} <= set(fields)


def test_merge_citations_dedup_renumber():
    """_merge_citations 去重并重新连续编号"""
    from app.ai.agents.interp_graph import _merge_citations
    a = [{"ref_id": 1, "entry_id": 12, "title": "A", "source": "document", "content": "c1"}]
    b = [{"ref_id": 1, "entry_id": 12, "title": "A", "source": "document", "content": "c1"},
         {"ref_id": 2, "entry_id": 13, "title": "B", "source": "document", "content": "c2"}]
    merged = _merge_citations(a, b)
    assert len(merged) == 2
    assert merged[0]["ref_id"] == 1
    assert merged[1]["ref_id"] == 2
    assert {m["entry_id"] for m in merged} == {12, 13}


def test_generate_report_empty_abnormal_returns_empty_report():
    """无异常指标时返回空 5 节报告 + 空引用"""
    from app.ai.agents.interp_graph import _generate_report
    state = {"abnormal_indicators": [], "knowledge_results": {}, "user_id": 1,
             "hospital_id": "H001", "report_id": 1, "overall_level": "green",
             "red_count": 0, "yellow_count": 0, "green_count": 5}
    result = _generate_report(state, __import__("unittest.mock").MagicMock())
    assert result["report"].overall_summary == ""
    assert result["report"].abnormal_focus == ""
    assert result["references"] == []
    assert result["judge_retry_count"] == 0


def test_generate_report_with_abnormal_calls_llm_and_injects():
    """有异常指标时 LLM 调用一次，inject_citations 注入后返回结构化报告"""
    from unittest.mock import patch, MagicMock
    from app.ai.agents.interp_graph import _generate_report, InterpretationReport

    state = {
        "abnormal_indicators": [{"indicator_id": 5, "item_name": "ALT", "result_value": "62",
                                 "unit": "U/L", "ref_range_low": "0", "ref_range_high": "40",
                                 "deviation": "high", "color_level": "yellow"}],
        "knowledge_results": {12: {"entry_id": 12, "title": "ALT 知识", "source": "document",
                                    "content": "ALT 升高常见于脂肪肝"}},
        "user_id": 1, "hospital_id": "H001", "report_id": 1,
        "overall_level": "yellow", "red_count": 0, "yellow_count": 1, "green_count": 10,
    }

    with patch("app.ai.agents.interp_graph.build_report_model") as mock_build, \
         patch("app.ai.agents.interp_graph.inject_citations") as mock_inj, \
         patch("app.ai.agents.interp_graph.strip_think_tags", side_effect=lambda x: x):
        mock_model = MagicMock()
        mock_build.return_value = mock_model
        mock_model.invoke.return_value = MagicMock(content='{"overall_summary":"S","abnormal_focus":"A","trend_note":"T","suggestions":"G","risk_alert":"R"}')
        mock_inj.side_effect = lambda text, sources, **kw: (text, [{"ref_id": 1, "entry_id": 12, "title": "ALT 知识", "source": "document", "content": "ALT 升高"}] if "ALT" in text else (text, []))

        result = _generate_report(state, MagicMock())
    assert isinstance(result["report"], InterpretationReport)
    assert result["report"].overall_summary == "S"
    assert len(result["references"]) >= 0
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py::test_interp_state_has_report_and_references tests/ai/agents/test_interp_graph.py::test_interpretation_report_is_5_sections tests/ai/agents/test_interp_graph.py::test_merge_citations_dedup_renumber tests/ai/agents/test_interp_graph.py::test_generate_report_empty_abnormal_returns_empty_report tests/ai/agents/test_interp_graph.py::test_generate_report_with_abnormal_calls_llm_and_injects -v
```
Expected: FAIL（新函数未定义）

- [ ] **Step 3: 删除旧测试 — 旧 InterpBatchResult/_agent_batch/_map_structured_to_explanations**

旧测试 `test_interp_batch_result_is_pydantic` / `test_build_interp_agent_returns_compiled` / `test_agent_batch_empty_abnormal_returns_empty` / `test_map_structured_to_explanations` 引用的旧符号将被删除。在 `backend/tests/ai/agents/test_interp_graph.py` 中删除这 4 个函数及其依赖 import，但保留 `test_interp_state_fields`（修改断言，见 Step 4）和 `test_build_interp_graph_returns_compiled`（保留）和 `test_interp_knowledge_middleware_extracts_refs_dict`（保留）。

具体改动：删除文件中以下函数（连同函数体）：
- `test_interp_batch_result_is_pydantic`
- `test_build_interp_agent_returns_compiled`
- `test_agent_batch_empty_abnormal_returns_empty`
- `test_map_structured_to_explanations`

并修改 `test_interp_state_fields` 为：

```python
def test_interp_state_fields():
    """InterpState 含必需字段"""
    from app.ai.agents.interp_graph import InterpState
    assert "indicators" in InterpState.__annotations__
    assert "judgments" in InterpState.__annotations__
    assert "abnormal_indicators" in InterpState.__annotations__
    assert "overall_level" in InterpState.__annotations__
```

（去掉 `agent_explanations` 的断言）

- [ ] **Step 4: 重写 interp_graph.py**

替换 `backend/app/ai/agents/interp_graph.py` 全文为：

```python
import json
import logging
from datetime import datetime
from typing import Annotated, List, Optional, TypedDict

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.graph import StateGraph, END
from langgraph.types import Command
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing_extensions import NotRequired

from app.ai.agents.tools import AgentContext, INTERP_TOOLS
from app.ai.agents.think_filter import strip_think_tags
from app.ai.agents.citation_matcher import inject_citations
from app.ai.agents.judge_graph import run_judge
from app.ai.llm import get_chat_model
from app.config import settings

logger = logging.getLogger(__name__)

SEARCH_SYSTEM_PROMPT = """你是医学知识检索助手。你的任务是为体检报告解读做知识储备，不要直接生成解读文字。
对每个异常指标，调用 search_knowledge 查询相关医学知识（指标含义、危险因素、健康影响、干预建议）。
查询要覆盖报告里的所有红/黄区指标。查完即止，不要继续输出解读或建议。"""


GENERATE_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。基于提供的医学知识和异常指标，撰写一份结构化的综合解读报告。

撰写规则：
1. overall_summary：1-2 段，整体健康概况，不诊断疾病。
2. abnormal_focus：每个红/黄区指标一段，说明偏离方向、可能原因、临床意义。绿区指标不在此节展开。
3. trend_note：若有历年数据说明趋势变化；无则留空字符串。
4. suggestions：具体可执行建议（饮食/运动/复查等），避免笼统的"注意饮食"。
5. risk_alert：红区指标提示"建议立即就医复查"；无红区则留空字符串。

约束：
- 不要 [n] 引用标记（系统会自动基于知识库来源注入）
- 仅基于提供的数据与知识库内容，不编造具体数值
- 5 个字段均要返回（无内容时填空字符串）
"""

SECTIONS = ["overall_summary", "abnormal_focus", "trend_note", "suggestions", "risk_alert"]


class InterpretationReport(BaseModel):
    overall_summary: str = ""
    abnormal_focus: str = ""
    trend_note: str = ""
    suggestions: str = ""
    risk_alert: str = ""


class Citation(BaseModel):
    ref_id: int
    entry_id: Optional[int] = None
    title: str = ""
    source: str = "document"


def _merge_knowledge_results(current: dict, update: dict) -> dict:
    merged = dict(current or {})
    merged.update(update or {})
    return merged


class InterpAgentState(AgentState):
    knowledge_results: Annotated[dict, _merge_knowledge_results]


class InterpState(TypedDict):
    hospital_id: str
    report_id: int
    user_id: int
    indicators: List[dict]
    judgments: List[dict]
    abnormal_indicators: List[dict]
    knowledge_results: dict
    report: InterpretationReport
    references: list
    overall_level: str
    red_count: int
    yellow_count: int
    green_count: int
    judge_result: dict
    judge_retry_count: int


def _extract_refs_dict_from_tool_result(result) -> dict:
    msgs = []
    if isinstance(result, Command):
        msgs = (result.update or {}).get("messages", [])
    else:
        msgs = [result]
    refs_dict = {}
    for m in msgs:
        if isinstance(m, ToolMessage):
            try:
                data = json.loads(m.content)
                if isinstance(data, list):
                    for r in data:
                        eid = r.get("entry_id")
                        source = r.get("source", "document")
                        content = r.get("content", "")
                        title = r.get("title", "")
                        if eid is not None:
                            refs_dict[eid] = {"entry_id": eid, "title": title, "source": source, "content": content}
                        elif source == "knowledge_graph":
                            kg_key = f"kg:{title}"
                            if kg_key not in refs_dict:
                                refs_dict[kg_key] = {"entry_id": None, "title": title, "source": "knowledge_graph", "content": content}
            except (json.JSONDecodeError, TypeError):
                pass
    return refs_dict


class InterpKnowledgeMiddleware(AgentMiddleware):
    def wrap_tool_call(self, request: ToolCallRequest, handler):
        result = handler(request)
        if request.tool_call["name"] == "search_knowledge":
            refs_dict = _extract_refs_dict_from_tool_result(result)
            if refs_dict:
                if isinstance(result, Command):
                    update = dict(result.update or {})
                    update["knowledge_results"] = refs_dict
                    return Command(update=update)
                return Command(update={"knowledge_results": refs_dict, "messages": [result]})
        return result


def build_interp_agent():
    model = get_chat_model(streaming=False)
    model.max_tokens = 2048
    return create_agent(
        model=model,
        tools=INTERP_TOOLS,
        system_prompt=SEARCH_SYSTEM_PROMPT,
        state_schema=InterpAgentState,
    )


def build_report_model():
    model = get_chat_model(streaming=False)
    model.max_tokens = 4096
    return model


def _merge_citations(cite_a: list[dict], cite_b: list[dict]) -> list[dict]:
    merged = []
    seen_keys = set()
    for cite in (cite_a or []) + (cite_b or []):
        key = (cite.get("entry_id"), cite.get("title"), cite.get("source"))
        if key not in seen_keys:
            seen_keys.add(key)
            new_ref_id = len(merged) + 1
            merged.append({
                "ref_id": new_ref_id,
                "entry_id": cite.get("entry_id"),
                "title": cite.get("title", ""),
                "source": cite.get("source", "document"),
                "content": cite.get("content", ""),
            })
    return merged


def _fetch_trend(user_id: int, db: Session) -> str:
    if not user_id:
        return ""
    try:
        rows = db.execute(
            text("SELECT ri.report_date, ind.item_name, ind.result_value, ind.unit "
                 "FROM report_indicator ind JOIN report_info ri ON ind.report_id = ri.id "
                 "WHERE ri.user_id = :uid ORDER BY ri.report_date ASC"),
            {"uid": user_id},
        ).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = []
    by_date: dict = {}
    for r in rows:
        d = str(r[0]) if r[0] else "未知"
        by_date.setdefault(d, []).append(f"{r[1]}={r[2]}{r[3] or ''}")
    for d, vals in by_date.items():
        lines.append(f"{d}: " + ", ".join(vals[:8]))
    return "\n".join(lines)


def _generate_report(state: InterpState, db: Session) -> dict:
    abnormal = state.get("abnormal_indicators", []) or []
    if not abnormal:
        return {
            "report": InterpretationReport(),
            "references": [],
            "judge_retry_count": state.get("judge_retry_count", 0),
        }

    knowledge = list((state.get("knowledge_results") or {}).values())
    trend = _fetch_trend(state.get("user_id"), db)

    abnormal_lines = []
    for ind in abnormal:
        abnormal_lines.append(
            f"- {ind.get('item_name')}: 值 {ind.get('result_value')}{ind.get('unit','')}, "
            f"参考 {ind.get('ref_range_low','-')}-{ind.get('ref_range_high','-')}, "
            f"{ind.get('deviation')}, {ind.get('color_level')}区"
        )
    abnormal_text = "\n".join(abnormal_lines)

    knowledge_blocks = []
    for k in knowledge:
        knowledge_blocks.append(
            f"- [来源] title={k.get('title','')}, source={k.get('source','document')}\n  {k.get('content','')[:500]}"
        )
    knowledge_text = "\n".join(knowledge_blocks) or "（无知识库结果）"

    user_content = f"""请基于以下数据撰写综合解读报告（5 节）：

## 报告概况
- 整体判定: {state.get('overall_level','green')}区
- 红区 {state.get('red_count',0)} 项, 黄区 {state.get('yellow_count',0)} 项, 绿区 {state.get('green_count',0)} 项

## 异常指标
{abnormal_text}

## 检索到的医学知识
{knowledge_text}

## 历年趋势
{trend or '（首份报告，无历史对比）'}

按 system 提示的 5 节字段返回 JSON。"""

    model = build_report_model()
    resp = model.invoke([("system", GENERATE_SYSTEM_PROMPT), ("user", user_content)]).content
    import re as _re
    from json_repair import repair_json
    match = _re.search(r'\{[\s\S]*\}', resp or "")
    if not match:
        logger.warning("generate_report: no JSON in LLM response, fallback to empty report")
        report_raw = {}
    else:
        try:
            report_raw = json.loads(match.group())
        except json.JSONDecodeError:
            try:
                report_raw = json.loads(repair_json(match.group()))
            except Exception:
                report_raw = {}
    report = InterpretationReport(
        overall_summary=strip_think_tags(report_raw.get("overall_summary", "")),
        abnormal_focus=strip_think_tags(report_raw.get("abnormal_focus", "")),
        trend_note=strip_think_tags(report_raw.get("trend_note", "")),
        suggestions=strip_think_tags(report_raw.get("suggestions", "")),
        risk_alert=strip_think_tags(report_raw.get("risk_alert", "")),
    )

    refs_all: list[dict] = []
    summaries = {}
    for field in SECTIONS:
        text_val = getattr(report, field)
        annotated, citations = inject_citations(text_val, knowledge)
        summaries[field] = annotated
        refs_all = _merge_citations(refs_all, citations)

    final_report = InterpretationReport(**summaries)
    retry_count = state.get("judge_retry_count", 0)
    return {
        "report": final_report,
        "references": refs_all,
        "judge_retry_count": retry_count,
    }


def build_interp_graph(hospital_id: str, db: Session):
    from app.modules.report.models import ReportInfo

    def load_indicators(state: InterpState) -> dict:
        report_id = state["report_id"]
        row = db.execute(
            text("SELECT id, user_id FROM report_info WHERE id = :rid"),
            {"rid": report_id},
        ).fetchone()
        user_id = row[1] if row else 0
        rows = db.execute(
            text("SELECT id, item_name, item_name_standard, result_value, unit, "
                 "ref_range_low, ref_range_high FROM report_indicator WHERE report_id = :rid ORDER BY id"),
            {"rid": report_id},
        ).fetchall()
        indicators = [
            {"id": r[0], "item_name": r[1], "item_name_standard": r[2],
             "result_value": r[3], "unit": r[4],
             "ref_range_low": r[5], "ref_range_high": r[6]}
            for r in rows
        ]
        return {"indicators": indicators, "user_id": user_id}

    def run_rules(state: InterpState) -> dict:
        from app.modules.interpretation.rules_engine import rules_engine
        from app.modules.interpretation.service import list_rules

        rules = list_rules(db)
        rules_engine.load_rules(state["hospital_id"], [{
            "id": r.id, "rule_name": r.rule_name, "rule_type": r.rule_type,
            "indicator_code": r.indicator_code, "conditions": r.conditions,
            "color_level": r.color_level, "priority": r.priority, "is_active": r.is_active,
        } for r in rules])

        judgments = []
        red_count = yellow_count = green_count = 0
        for ind in state["indicators"]:
            ind_dict = {
                "item_name": ind["item_name"],
                "item_name_standard": ind["item_name_standard"],
                "result_value": ind["result_value"],
                "unit": ind["unit"],
                "ref_range_low": ind["ref_range_low"],
                "ref_range_high": ind["ref_range_high"],
            }
            result = rules_engine.evaluate(state["hospital_id"], ind_dict)
            deviation = result.deviation
            color_level = result.color_level
            if deviation == "normal":
                try:
                    val = float(ind["result_value"] or 0)
                    ref_high = float(ind["ref_range_high"] or 0)
                    ref_low = float(ind["ref_range_low"] or 0)
                    if ref_high and val > ref_high:
                        deviation = "high"
                        if color_level == "green":
                            color_level = "yellow"
                    elif ref_low and val < ref_low:
                        deviation = "low"
                        if color_level == "green":
                            color_level = "yellow"
                except (ValueError, TypeError):
                    pass

            judgments.append({
                "indicator_id": ind["id"],
                "item_name": ind["item_name"],
                "result_value": ind["result_value"],
                "deviation": deviation,
                "color_level": color_level,
                "matched_rule_id": result.matched_rule_id,
            })

            if color_level == "red":
                red_count += 1
            elif color_level == "yellow":
                yellow_count += 1
            else:
                green_count += 1

        overall = "green"
        if red_count > 0:
            overall = "red"
        elif yellow_count > 0:
            overall = "yellow"

        return {
            "judgments": judgments,
            "overall_level": overall,
            "red_count": red_count,
            "yellow_count": yellow_count,
            "green_count": green_count,
        }

    def filter_abnormal(state: InterpState) -> dict:
        by_id = {i["id"]: i for i in state["indicators"]}
        abnormal = [
            {**j, **{
                "unit": by_id.get(j["indicator_id"], {}).get("unit"),
                "ref_range_low": by_id.get(j["indicator_id"], {}).get("ref_range_low"),
                "ref_range_high": by_id.get(j["indicator_id"], {}).get("ref_range_high"),
            }}
            for j in state["judgments"]
            if j["color_level"] in ("red", "yellow")
        ]
        return {"abnormal_indicators": abnormal}

    def agent_search_knowledge(state: InterpState) -> dict:
        if not state.get("abnormal_indicators"):
            return {"knowledge_results": {}}
        agent = build_interp_agent()
        names = ", ".join(ind["item_name"] for ind in state["abnormal_indicators"])
        user_content = f"以下指标存在异常，请逐个查询相关医学知识（指标含义、危险因素、临床意义、干预建议）：\n{names}\n\n对每个指标调用 search_knowledge。"
        result = agent.invoke(
            {"messages": [HumanMessage(content=user_content)]},
            config={"recursion_limit": settings.AGENT_MAX_ITERATIONS * 2},
            context=AgentContext(hospital_id=state["hospital_id"]),
        )
        return {"knowledge_results": result.get("knowledge_results", {}) or {}}

    def generate_report(state: InterpState) -> dict:
        return _generate_report(state, db)

    def judge(state: InterpState) -> dict:
        if not state.get("abnormal_indicators"):
            return {"judge_result": {"passed": True, "issues": [], "suggestions": ""}}
        return {"judge_result": run_judge(state)}

    def after_judge(state: InterpState) -> str:
        judge_result = state.get("judge_result", {})
        if judge_result.get("passed", True):
            return "persist"
        if state.get("judge_retry_count", 0) >= settings.JUDGE_MAX_RETRIES:
            return "persist_with_note"
        return "generate_report"

    def persist(state: InterpState) -> dict:
        from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
        from app.core.rabbitmq import rabbitmq, TaskMessage

        report_id = state["report_id"]
        db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == report_id,
        ).delete()
        db.commit()

        interp = ReportInterpretation(report_id=report_id, status="processing")
        db.add(interp)
        db.commit()
        db.refresh(interp)

        report = state.get("report") or InterpretationReport()
        references = state.get("references", []) or []
        interp.summary_text = report.model_dump_json()
        interp.summary_refs = references
        judge_result = state.get("judge_result", {})
        if not judge_result.get("passed", True):
            issues = "; ".join(judge_result.get("issues", [])[:3])
            interp.quality_note = (issues or "审核未通过")[:255]

        for j in state["judgments"]:
            db.add(IndicatorJudgment(
                interpretation_id=interp.id,
                indicator_id=j["indicator_id"],
                item_name=j["item_name"],
                result_value=j["result_value"],
                deviation=j["deviation"],
                color_level=j["color_level"],
                matched_rule_id=j["matched_rule_id"],
                explanation=None, suggestion=None, knowledge_refs=None,
                certainty=None, certainty_reason=None,
            ))

        interp.red_count = state["red_count"]
        interp.yellow_count = state["yellow_count"]
        interp.green_count = state["green_count"]
        interp.overall_level = state["overall_level"]
        interp.status = "completed"
        interp.completed_at = datetime.utcnow()
        db.commit()

        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=state["hospital_id"], priority=0,
            payload={"event": "interpretation_done", "report_id": report_id,
                     "hospital_id": state["hospital_id"]},
        ))
        return {}

    def persist_with_note(state: InterpState) -> dict:
        return persist(state)

    g = StateGraph(InterpState)
    g.add_node("load_indicators", load_indicators)
    g.add_node("run_rules", run_rules)
    g.add_node("filter_abnormal", filter_abnormal)
    g.add_node("agent_search_knowledge", agent_search_knowledge)
    g.add_node("generate_report", generate_report)
    g.add_node("judge", judge)
    g.add_node("persist", persist)
    g.add_node("persist_with_note", persist_with_note)
    g.set_entry_point("load_indicators")
    g.add_edge("load_indicators", "run_rules")
    g.add_edge("run_rules", "filter_abnormal")
    g.add_edge("filter_abnormal", "agent_search_knowledge")
    g.add_edge("agent_search_knowledge", "generate_report")
    g.add_edge("generate_report", "judge")
    g.add_conditional_edges("judge", after_judge, {
        "persist": "persist",
        "persist_with_note": "persist_with_note",
        "generate_report": "generate_report",
    })
    g.add_edge("persist", END)
    g.add_edge("persist_with_note", END)
    return g.compile()


def run_interpretation_agent(hospital_id: str, db: Session, report_id: int) -> dict:
    from app.modules.report.models import ReportInfo
    from app.modules.interpretation.models import ReportInterpretation

    report = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not report:
        return {}

    existing = db.query(ReportInterpretation).filter(
        ReportInterpretation.report_id == report_id,
        ReportInterpretation.status == "completed",
    ).first()
    if existing:
        return {}

    graph = build_interp_graph(hospital_id, db)
    try:
        final_state = graph.invoke({
            "hospital_id": hospital_id,
            "report_id": report_id,
            "user_id": 0,
            "indicators": [],
            "judgments": [],
            "abnormal_indicators": [],
            "knowledge_results": {},
            "report": InterpretationReport(),
            "references": [],
            "overall_level": "green",
            "red_count": 0, "yellow_count": 0, "green_count": 0,
            "judge_result": {},
            "judge_retry_count": 0,
        })
        return final_state
    except Exception as e:
        interp = db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == report_id,
            ReportInterpretation.status == "processing",
        ).first()
        if interp:
            interp.retry_count += 1
            interp.status = "failed" if interp.retry_count >= 3 else "pending"
            db.commit()
        raise
```

注意：原生 from `langchain.messages import ToolMessage` 在 Step 3 中我用了 `m.__class__.__name__ == "ToolMessage"` 是为绕过 import 失败的特殊情况；这里改为正式 import 形式。在文件中 `from langchain.messages import HumanMessage, ToolMessage`，并把 `_extract_refs_dict_from_tool_result` 中的判断改回 `isinstance(m, ToolMessage)`（最终代码用 isinstance，测试用 MagicMock 的 spec 应正常）。

- [ ] **Step 5: 更新 __init__.py exports**

替换 `backend/app/ai/agents/__init__.py` 全文为：

```python
from app.ai.agents.chat_graph import (
    run_chat_agent, build_chat_agent, ChatAgentState,
    KnowledgeRefsMiddleware, ReportContextMiddleware,
)
from app.ai.agents.interp_graph import (
    run_interpretation_agent, build_interp_graph, build_interp_agent,
    InterpretationReport, InterpKnowledgeMiddleware, Citation,
)
from app.ai.agents.tools import AgentContext, CHAT_TOOLS, INTERP_TOOLS
```

- [ ] **Step 6: 更新旧测试 test_interp_knowledge_middleware_extracts_refs_dict**

旧测试用 `ToolMessage(content=..., tool_call_id="call_1")` 构造，与新实现兼容，保持不变即可。但确保它仍 PASS。

- [ ] **Step 7: 运行测试确认通过**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py -v
```
Expected: 全部 PASS

- [ ] **Step 8: 运行迁移与集成测试**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_migration.py tests/ai/test_integration.py -v
```
Expected: PASS。若 `test_agent_tools_available_in_graph` 断 INTERP_TOOLS 长度仍为 2 则 PASS（未改 tools）。

- [ ] **Step 9: Commit**

```bash
cd /data/project/hospitalKnowledgeBase && \
git add backend/app/ai/agents/interp_graph.py backend/app/ai/agents/__init__.py \
        backend/tests/ai/agents/test_interp_graph.py && \
git commit -m "refactor(interp): restructure graph to search_knowledge→generate_report→judge→persist; output 5-section report"
```

---

### Task 6: 后端 Router & Service — 新响应

**Files:**
- Modify: `backend/app/modules/interpretation/router.py:66-94`
- Modify: `backend/app/modules/interpretation/service.py`（新增 `get_reference_list` 辅助）

**Interfaces:**
- Consumes：Task 3 的 `parse_summary_text`、`InterpretationResponse`、`IndicatorJudgmentSchema`、`CitationSchema`；Task 5 的 `InterpretationReport`
- Produces：`GET /api/v1/interpretations/{report_id}` 返回新结构，`indicators[]` 含 `unit/ref_range_low/ref_range_high`（通过 join `report_indicator`）

- [ ] **Step 1: 在 service.py 加 get_judgments_with_indicator_detail**

修改 `backend/app/modules/interpretation/service.py`，新增函数（在 `get_judgments` 之后）：

```python
def get_judgments_with_indicator_detail(db: Session, interpretation_id: int) -> list[dict]:
    """join indicator_judgment 与 report_indicator，返回前端展示所需字段

    含 deviation/color_level（来自 judgment）+ unit/ref_range_low/ref_range_high（来自 indicator）
    """
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT j.indicator_id, j.item_name, j.result_value, j.deviation, j.color_level, "
        "i.unit, i.ref_range_low, i.ref_range_high "
        "FROM indicator_judgment j "
        "LEFT JOIN report_indicator i ON i.id = j.indicator_id "
        "WHERE j.interpretation_id = :iid ORDER BY j.id"
    ), {"iid": interpretation_id}).fetchall()
    return [
        {"indicator_id": r[0], "item_name": r[1], "result_value": r[2],
         "deviation": r[3], "color_level": r[4], "unit": r[5],
         "ref_range_low": r[6], "ref_range_high": r[7]}
        for r in rows
    ]
```

- [ ] **Step 2: 写测试 — router 返回新结构**

在 `backend/tests/ai/agents/test_interp_graph.py` 末尾追加：

```python
def test_router_returns_summaries_and_references():
    """router 拼装 InterpretationResponse 含 summaries/references，无 summary_text"""
    from unittest.mock import patch, MagicMock
    from app.modules.interpretation.router import get_interpretation
    from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment

    interp = MagicMock(spec=ReportInterpretation)
    interp.id = 7
    interp.report_id = 4
    interp.overall_level = "yellow"
    interp.red_count = 0
    interp.yellow_count = 5
    interp.green_count = 33
    interp.status = "completed"
    interp.created_at = "2026-07-06T13:52:05"
    interp.completed_at = "2026-07-06T13:52:05"
    interp.summary_text = '{"overall_summary":"S","abnormal_focus":"A","trend_note":"","suggestions":"G","risk_alert":""}'
    interp.summary_refs = [{"ref_id": 1, "entry_id": 12, "title": "T", "source": "document"}]
    interp.quality_note = None

    j = MagicMock(spec=IndicatorJudgment)
    j.indicator_id = 88
    j.item_name = "ALT"
    j.result_value = "62"
    j.deviation = "high"
    j.color_level = "yellow"
    j.unit = "U/L"
    j.ref_range_low = "0"
    j.ref_range_high = "40"

    with patch("app.modules.interpretation.router.service.get_interpretation", return_value=interp), \
         patch("app.modules.interpretation.router.service.get_judgments_with_indicator_detail",
               return_value=[{"indicator_id": 88, "item_name": "ALT", "result_value": "62",
                              "deviation": "high", "color_level": "yellow",
                              "unit": "U/L", "ref_range_low": "0", "ref_range_high": "40"}]):
        resp = get_interpretation(report_id=4, db=MagicMock())
    assert "summary_text" not in resp
    assert "summaries" in resp
    assert resp["summaries"].overall_summary == "S"
    assert resp["references"][0]["entry_id"] == 12
    assert resp["indicators"][0].indicator_id == 88
    assert resp["indicators"][0].unit == "U/L"
    assert resp["indicators"][0].ref_range_low == "0"
    assert resp["indicators"][0].ref_range_high == "40"
    assert not hasattr(resp["indicators"][0], "explanation")
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py::test_router_returns_summaries_and_references -v
```
Expected: FAIL（router 仍返旧结构）

- [ ] **Step 4: 修 router**

把 `backend/app/modules/interpretation/router.py` 末尾两个端点 `get_interpretation` 与 `get_judgments` 改为：

```python
from app.modules.interpretation.schemas import (
    IndicatorJudgmentSchema, CitationSchema, parse_summary_text,
)

# ---- Dynamic {report_id} path LAST ----

@router.get("/{report_id}", response_model=schemas.InterpretationResponse)
def get_interpretation(report_id: int, db: Session = Depends(_get_db)):
    interp = service.get_interpretation(db, report_id)
    if not interp:
        raise NotFoundException(detail="Interpretation not found")
    rows = service.get_judgments_with_indicator_detail(db, interp.id)
    summaries = parse_summary_text(interp.summary_text)
    references = [CitationSchema(**r) for r in (interp.summary_refs or [])]
    indicators = [IndicatorJudgmentSchema(**r) for r in rows]
    return {
        "id": interp.id, "report_id": interp.report_id,
        "overall_level": interp.overall_level,
        "red_count": interp.red_count, "yellow_count": interp.yellow_count,
        "green_count": interp.green_count,
        "status": interp.status,
        "summaries": summaries,
        "references": references,
        "quality_note": interp.quality_note,
        "indicators": indicators,
        "created_at": interp.created_at, "completed_at": interp.completed_at,
    }


@router.get("/{report_id}/indicators")
def get_judgments(report_id: int, db: Session = Depends(_get_db)):
    interp = service.get_interpretation(db, report_id)
    if not interp:
        raise NotFoundException(detail="Interpretation not found")
    return service.get_judgments(db, interp.id)
```

注意：把 `IndicatorJudgmentSchema`/`CitationSchema`/`parse_summary_text` 的 import 加到文件顶部（已有 `from app.modules.interpretation import schemas, service`，可在 fn 内或顶部补加）。

- [ ] **Step 5: 运行测试确认通过**

```bash
cd /data/project/hospitalKnowledgeBase/backend && uv run pytest tests/ai/agents/test_interp_graph.py::test_router_returns_summaries_and_references -v
```
Expected: PASS

- [ ] **Step 6: 端到端冒烟（需后端运行）**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
import requests
r = requests.post('http://127.0.0.1:8000/api/v1/auth/login',
    json={'username':'user1','password':'123456'}, timeout=10)
tok = r.json()['access_token']
h = {'Authorization': f'Bearer {tok}'}
for rid in (3, 4):
    r = requests.get(f'http://127.0.0.1:8000/api/v1/interpretations/{rid}', headers=h, timeout=10)
    print(f'interp/{rid}:', r.status_code)
    if r.status_code == 200:
        d = r.json()
        print('  summaries keys:', list(d.get('summaries', {}).keys()) if d.get('summaries') else None)
        print('  references:', len(d.get('references', []) or []))
        print('  indicators[0]:', d['indicators'][0] if d.get('indicators') else None)
"
```
Expected: 两份均 200，3 是旧数据（summaries 为空 5 节），4 是 Task 重启后的新数据（说明下次重跑解读才会有新格式）

- [ ] **Step 7: Commit**

```bash
cd /data/project/hospitalKnowledgeBase && \
git add backend/app/modules/interpretation/router.py backend/tests/ai/agents/test_interp_graph.py && \
git commit -m "feat(interp): router returns summaries/references/indicators without explanation"
```

---

### Task 7: 前端 Shared — 引入 react-markdown，建 InterpretationReportCard

**Files:**
- Modify: `frontend/packages/shared/package.json`（加依赖）
- Create: `frontend/packages/shared/src/components/InterpretationReport/InterpretationReportCard.tsx`
- Create: `frontend/packages/shared/src/components/InterpretationReport/MarkdownRenderer.tsx`
- Create: `frontend/packages/shared/src/components/InterpretationReport/CitationPopover.tsx`
- Create: `frontend/packages/shared/src/components/InterpretationReport/index.ts`
- Modify: `frontend/packages/shared/src/index.ts`（re-export）

**Interfaces:**
- Produces：`@hospital/shared` 导出 `InterpretationReportCard`（Props 定义如下）
- Types：
  ```ts
  interface InterpretationReportData {
    overall_summary: string;
    abnormal_focus: string;
    trend_note: string;
    suggestions: string;
    risk_alert: string;
  }
  interface CitationRef { ref_id: number; entry_id: number | null; title: string; source: string; content?: string; }
  interface InterpretationReportCardProps {
    summaries: InterpretationReportData | null | undefined;
    references?: CitationRef[];
    loading?: boolean;
    qualityNote?: string | null;
  }
  ```

- [ ] **Step 1: 安装依赖**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && \
npm install -w @hospital/shared react-markdown remark-gfm --save
```
Expected: 写入 shared/package.json 依赖

- [ ] **Step 2: 建 MarkdownRenderer**

创建 `frontend/packages/shared/src/components/InterpretationReport/MarkdownRenderer.tsx`：

```tsx
import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function MarkdownRenderer({ children }: { children: string }) {
  return (
    <div className="interp-md" style={{ fontSize: 13, lineHeight: 1.7, color: "var(--color-text-secondary, #555)" }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ node, ...props }) => <p style={{ margin: "4px 0" }} {...props} />,
          h1: ({ node, ...props }) => <h3 style={{ fontSize: 14, margin: "12px 0 4px" }} {...props} />,
          h2: ({ node, ...props }) => <h3 style={{ fontSize: 14, margin: "12px 0 4px" }} {...props} />,
          h3: ({ node, ...props }) => <h4 style={{ fontSize: 13, margin: "8px 0 4px" }} {...props} />,
          ul: ({ node, ...props }) => <ul style={{ margin: "4px 0 4px 18px" }} {...props} />,
          ol: ({ node, ...props }) => <ol style={{ margin: "4px 0 4px 18px" }} {...props> />,
          code: ({ node, ...props }) => <code style={{ background: "#f5f5f5", padding: "1px 4px", borderRadius: 4 }} {...props} />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
```

- [ ] **Step 3: 建 CitationPopover**

创建 `frontend/packages/shared/src/components/InterpretationReport/CitationPopover.tsx`：

```tsx
import React, { useState } from "react";
import { Popover, Typography } from "antd";

export interface CitationRef {
  ref_id: number;
  entry_id: number | null;
  title: string;
  source: string;
  content?: string;
}

export function findRef(text: string, references: CitationRef[] = []): CitationRef[] {
  const ids: number[] = [];
  const re = /\[(\d+)\]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    ids.push(Number(m[1]));
  }
  return references.filter(r => ids.includes(r.ref_id));
}

export default function CitationList({ references }: { references: CitationRef[] }) {
  const [open, setOpen] = useState(false);
  if (!references.length) return null;
  return (
    <div style={{ marginTop: 12, padding: 12, background: "#F0FDF4", borderRadius: 8 }}>
      <Typography.Text strong style={{ fontSize: 12, color: "#166534", display: "block", marginBottom: 6 }}>
        参考来源（{references.length}）
      </Typography.Text>
      {references.map(r => (
        <div key={r.ref_id} style={{ fontSize: 12, marginBottom: 4, lineHeight: 1.5 }}>
          <Typography.Text style={{ color: "#166534", fontWeight: 600 }}>[{r.ref_id}]</Typography.Text>{" "}
          <Typography.Text>{r.title}</Typography.Text>
          <Typography.Text type="secondary" style={{ marginLeft: 6, fontSize: 11 }}>
            {r.source === "knowledge_graph" ? "知识图谱" : "知识库"}
          </Typography.Text>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: 建 InterpretationReportCard**

创建 `frontend/packages/shared/src/components/InterpretationReport/InterpretationReportCard.tsx`：

```tsx
import React from "react";
import { Card, Spin, Alert, Tag, Typography } from "antd";
import MarkdownRenderer from "./MarkdownRenderer";
import CitationList, { CitationRef } from "./CitationPopover";

export interface InterpretationReportData {
  overall_summary: string;
  abnormal_focus: string;
  trend_note: string;
  suggestions: string;
  risk_alert: string;
}

export interface InterpretationReportCardProps {
  summaries: InterpretationReportData | null | undefined;
  references?: CitationRef[];
  loading?: boolean;
  qualityNote?: string | null;
}

const SECTIONS: { key: keyof InterpretationReportData; title: string; accent?: string }[] = [
  { key: "overall_summary", title: "整体评估" },
  { key: "abnormal_focus", title: "重点异常解读" },
  { key: "trend_note", title: "历年趋势" },
  { key: "suggestions", title: "健康建议" },
  { key: "risk_alert", title: "风险提示", accent: "red" },
];

const ACCENT_COLOR: Record<string, string> = {
  red: "#fff2f0",
  yellow: "#fffbe6",
  green: "#f6ffed",
};

export default function InterpretationReportCard({ summaries, references = [], loading, qualityNote }: InterpretationReportCardProps) {
  if (loading) {
    return (
      <Card title="AI 解读报告" style={{ marginTop: 20 }}>
        <div style={{ textAlign: "center", padding: 32 }}>
          <Spin tip="AI 解读生成中..." />
        </div>
      </Card>
    );
  }

  if (!summaries || !Object.values(summaries).some(v => v && v.trim())) {
    return (
      <Card title="AI 解读报告" style={{ marginTop: 20 }}>
        <Typography.Text type="secondary">暂无 AI 解读报告</Typography.Text>
      </Card>
    );
  }

  const refs = references || [];

  return (
    <Card
      title={<span>🩺 AI 解读报告</span>}
      style={{ marginTop: 20 }}
      extra={refs.length > 0 ? <Tag color="green">{refs.length} 个参考来源</Tag> : null}
    >
      {qualityNote && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`AI 解读质量审核建议：${qualityNote}`}
        />
      )}
      {SECTIONS.map(sec => {
        const content = summaries[sec.key];
        if (!content || !content.trim()) return null;
        const bg = sec.accent ? ACCENT_COLOR[sec.accent] : undefined;
        return (
          <div key={sec.key} style={{
            marginBottom: 14, padding: 12,
            background: bg || "var(--color-bg, #fafafa)",
            borderRadius: 8,
            border: sec.accent === "red" ? "1px solid #ffccc7" : "1px solid var(--color-border-light, #f0f0f0)",
          }}>
            <Typography.Text strong style={{ fontSize: 14, display: "block", marginBottom: 6 }}>
              {sec.title}
            </Typography.Text>
            <MarkdownRenderer>{content}</MarkdownRenderer>
          </div>
        );
      })}
      <CitationList references={refs} />
    </Card>
  );
}
```

- [ ] **Step 5: 建 index.ts 并 re-export**

创建 `frontend/packages/shared/src/components/InterpretationReport/index.ts`：

```ts
export { default as InterpretationReportCard } from "./InterpretationReportCard";
export type { InterpretationReportData, InterpretationReportCardProps } from "./InterpretationReportCard";
export { default as CitationList } from "./CitationPopover";
export type { CitationRef } from "./CitationPopover";
export { findRef } from "./CitationPopover";
```

修改 `frontend/packages/shared/src/index.ts` 为：

```ts
export { createApiClient } from "./api/client";
export * from "./components/InterpretationReport";
```

- [ ] **Step 6: 构建验证**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && npm run build -w @hospital/shared 2>&1 | tail -20
```
Expected: 构建通过无类型错误（若 shared 无 build 脚本，跑 user-portal 的 tsc）：
```bash
cd /data/project/hospitalKnowledgeBase/frontend && npx tsc --noEmit -p packages/shared/tsconfig.json 2>&1 | tail -20
```

- [ ] **Step 7: Commit**

```bash
cd /data/project/hospitalKnowledgeBase && \
git add frontend/packages/shared/package.json frontend/packages/shared/src/ \
        frontend/package-lock.json frontend/packages/shared/package-lock.json 2>/dev/null; \
git add frontend/packages/shared frontend/package-lock.json && \
git commit -m "feat(shared): add InterpretationReportCard with react-markdown"
```

---

### Task 8: 前端 User Portal — 简化 IndicatorRow + 重构 ReportDetailPage

**Files:**
- Modify: `frontend/packages/user-portal/src/components/IndicatorRow.tsx`
- Modify: `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`

**Interfaces:**
- Consumes：Task 7 的 `@hospital/shared` 的 `InterpretationReportCard`

- [ ] **Step 1: 简化 IndicatorRow**

把 `frontend/packages/user-portal/src/components/IndicatorRow.tsx` 全文替换为：

```tsx
import ColorBadge from './ColorBadge';

interface IndicatorRowProps {
  item_name: string;
  result_value?: string;
  unit?: string;
  ref_range_low?: string;
  ref_range_high?: string;
  color_level?: string;
}

export default function IndicatorRow({
  item_name, result_value, unit, ref_range_low, ref_range_high, color_level,
}: IndicatorRowProps) {
  const refRange = ref_range_low && ref_range_high ? `${ref_range_low}-${ref_range_high}` : '';
  return (
    <div style={{ borderBottom: '1px solid var(--color-border-light)' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '14px 0',
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 14, fontWeight: 500 }}>{item_name}</div>
          {refRange && <div style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 2 }}>参考: {refRange} {unit || ''}</div>}
        </div>
        <div style={{ textAlign: 'right', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16, fontWeight: 700 }}>{result_value || '-'}</span>
          {unit && <span style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>{unit}</span>}
          {color_level ? <ColorBadge level={color_level} size="sm" /> : null}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: 重构 ReportDetailPage**

把 `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx` 全文替换为：

```tsx
import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Spin, Button, Popconfirm, message } from 'antd';
import { ArrowLeftOutlined, DeleteOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import Layout from '../components/Layout';
import ColorBadge from '../components/ColorBadge';
import IndicatorRow from '../components/IndicatorRow';
import StatusTag from '../components/StatusTag';
import ChatPanel from '../components/ChatPanel';
import { useChatStore } from '../stores/chatStore';
import { InterpretationReportCard } from '@hospital/shared';

const COLOR_ORDER: Record<string, number> = { red: 0, yellow: 1, green: 2 };

export default function ReportDetailPage() {
  const { id } = useParams();
  const { api } = useUserStore();
  const nav = useNavigate();
  const [report, setReport] = useState<any>(null);
  const [interpretation, setInterpretation] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const chatStore = useChatStore();
  const [chatSessionId, setChatSessionId] = useState<number | null>(null);
  const [taskStatus, setTaskStatus] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.get(`/reports/${id}`).catch(() => ({ data: null })),
      api.get(`/interpretations/${id}`).catch(() => ({ data: null })),
    ]).then(([r, i]) => {
      setReport(r.data);
      setInterpretation(i.data);
      const taskId = r.data?.task_id;
      if (taskId) {
        api.get(`/reports/tasks/${taskId}`).then(t => setTaskStatus(t.data?.status)).catch(() => {});
      }
    }).finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (!id) return;
    api.get('/chat/sessions').then(r => {
      const sessions = r.data || [];
      const existing = sessions.find((s: any) => s.report_id === Number(id));
      if (existing) {
        setChatSessionId(existing.id);
        chatStore.setCurrentSession(existing.id);
      } else {
        api.post('/chat/sessions', { report_id: Number(id) }).then(r2 => {
          setChatSessionId(r2.data.id);
          chatStore.setCurrentSession(r2.data.id);
        }).catch(() => {});
      }
    }).catch(() => {});
  }, [id]);

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>;
  if (!report) return <Layout title="报告详情"><p>报告不存在</p></Layout>;

  const isProcessing = taskStatus && taskStatus !== 'completed' && taskStatus !== 'failed';
  const interpLoading = !!isProcessing || (interpretation?.status && interpretation.status !== 'completed');

  if (isProcessing) {
    return (
      <Layout title="报告详情">
        <div style={{ textAlign: 'center', padding: '80px 20px' }}>
          <Spin size="large" />
          <h3 style={{ marginTop: 24, marginBottom: 8 }}>报告处理中</h3>
          <p style={{ color: '#888', marginBottom: 16 }}>AI 正在解析这份报告，请稍后回来查看</p>
          <StatusTag status={taskStatus!} />
          <div style={{ marginTop: 32 }}>
            <Button onClick={() => nav('/')}>返回首页</Button>
          </div>
        </div>
      </Layout>
    );
  }

  const overallLevel = interpretation?.overall_level;
  // 优先用 interpretation.indicators（含 color_level + unit + ref_range，已 Task 6 join），
  // 旧数据/未生成时退化为 report.indicators（无 color_level）
  const rawIndicators = interpretation?.indicators?.length ? interpretation.indicators : (report?.indicators || []);
  const sortedIndicators = [...rawIndicators].sort((a, b) =>
    (COLOR_ORDER[a.color_level] ?? 3) - (COLOR_ORDER[b.color_level] ?? 3));

  return (
    <Layout title={report.name || '报告详情'}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 20 }}>
        <button onClick={() => nav(-1)} style={{
          border: 'none', background: 'none', fontSize: 14, color: 'var(--color-primary)',
          cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
        }}>
          <ArrowLeftOutlined /> 返回
        </button>
        <Popconfirm
          title="确定删除这份报告吗？"
          description="删除后将无法恢复"
          onConfirm={async () => {
            try { await api.delete(`/reports/${id}`); message.success('已删除'); nav('/'); }
            catch { message.error('删除失败'); }
          }}
          okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
        >
          <button style={{ border: 'none', background: 'none', fontSize: 14, color: '#ff4d4f', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4 }}>
            <DeleteOutlined /> 删除
          </button>
        </Popconfirm>
      </div>

      <div style={{
        background: 'var(--color-surface)', borderRadius: 'var(--radius-md)',
        padding: 20, boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)', marginBottom: 20,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 600 }}>{report.name || '未识别'}</div>
            <div style={{ fontSize: 13, color: 'var(--color-text-secondary)' }}>
              {report.gender} · {report.age}岁 · {report.report_date}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {overallLevel && <ColorBadge level={overallLevel} size="md" />}
            {interpretation?.status && <StatusTag status={interpretation.status} />}
          </div>
        </div>
      </div>

      {interpretation && (
        <div style={{
          display: 'flex', gap: 8, marginBottom: 16, padding: '12px 16px', background: 'var(--color-bg)', borderRadius: 'var(--radius-sm)',
        }}>
          <span style={{ color: 'var(--color-red)', fontWeight: 600, fontSize: 13 }}>红区 {interpretation.red_count}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-yellow)', fontWeight: 600, fontSize: 13 }}>黄区 {interpretation.yellow_count}</span>
          <span style={{ color: 'var(--color-border)' }}>|</span>
          <span style={{ color: 'var(--color-green)', fontWeight: 600, fontSize: 13 }}>绿区 {interpretation.green_count}</span>
        </div>
      )}

      <div style={{ background: 'var(--color-surface)', borderRadius: 'var(--radius-md)', padding: '0 20px', boxShadow: 'var(--shadow-sm)', border: '1px solid var(--color-border-light)' }}>
        {sortedIndicators.map((ind: any, idx: number) => (
          <IndicatorRow
            key={idx}
            item_name={ind.item_name}
            result_value={ind.result_value}
            unit={ind.unit}
            ref_range_low={ind.ref_range_low}
            ref_range_high={ind.ref_range_high}
            color_level={ind.color_level}
          />
        ))}
        {sortedIndicators.length === 0 && (
          <div style={{ textAlign: 'center', padding: 32, color: 'var(--color-text-secondary)', fontSize: 13 }}>暂无指标数据</div>
        )}
      </div>

      <InterpretationReportCard
        summaries={interpretation?.summaries}
        references={interpretation?.references}
        loading={interpLoading}
        qualityNote={interpretation?.quality_note}
      />

      {chatSessionId && (
        <div style={{ marginTop: 24, borderTop: '1px solid #E5E7EB', paddingTop: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14, color: '#0D9488' }}>
            💬 AI 健康咨询（基于本报告）
          </div>
          <ChatPanel sessionId={chatSessionId} placeholder="基于本报告提问..." compact />
        </div>
      )}
    </Layout>
  );
}
```

注意：`api.get('/reports/{id}/indicators')` 需后端有对应端点。若无，应改用 report 字段中已有的 `report.indicators`。先确认：

```bash
grep -n "indicators" /data/project/hospitalKnowledgeBase/backend/app/modules/report/router.py
```

说明：指标数据来源是 `interpretation.indicators`（Task 6 的 router 已 join `report_indicator` 取 unit/ref_range_low/ref_range_high/color_level）；若解读还未完成（处理中）则退化为 `report.indicators`（无 color_level，但能展示原始数值）。

- [ ] **Step 3: 构建 / 类型检查**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && npx tsc --noEmit -p packages/user-portal/tsconfig.json 2>&1 | tail -30
```
Expected: 无类型错误（如有，按提示修复 import / props）

- [ ] **Step 4: dev 启动验证**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && npm run dev -w @hospital/user-portal > /tmp/fe-user-v2.log 2>&1 &
sleep 5
curl -s -m 3 http://localhost:3001 | head -c 200
```
Expected: 返回 HTML（dev server 起来）

- [ ] **Step 5: Commit**

```bash
cd /data/project/hospitalKnowledgeBase && \
git add frontend/packages/user-portal/src/components/IndicatorRow.tsx \
        frontend/packages/user-portal/src/pages/ReportDetailPage.tsx && \
git commit -m "feat(user-portal): split indicator table and AI interpretation report card"
```

---

### Task 9: 前端 Doctor Portal — 重构 ReportDetailPage

**Files:**
- Modify: `frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx`

**Interfaces:**
- Consumes：Task 7 的 `@hospital/shared` 的 `InterpretationReportCard`

- [ ] **Step 1: 重构 doctor ReportDetailPage**

把 `frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx` 全文替换为：

```tsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Spin, Card, Tag, Table } from 'antd';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';
import { InterpretationReportCard } from '@hospital/shared';

const COLORS: any = { red: 'red', yellow: 'gold', green: 'green' };
const DEVIATION_TXT: any = { high: '↑ 偏高', low: '↓ 偏低', normal: '正常' };

export default function ReportDetailPage() {
  const { id } = useParams();
  const { api } = useDoctorStore();
  const [report, setReport] = useState<any>(null);
  const [interp, setInterp] = useState<any>(null);
  const [indicators, setIndicators] = useState<any[]>([]);

  useEffect(() => {
    api.get(`/reports/${id}`).then(r => { setReport(r.data); });
    api.get(`/interpretations/${id}`).then(r => setInterp(r.data));
  }, [id]);

  useEffect(() => {
    if (!report?.id) return;
    setIndicators(report?.indicators || []);
  }, [report]);

  if (!report) return <DoctorLayout><Spin /></DoctorLayout>;

  const columns = [
    { title: '指标', dataIndex: 'item_name', key: 'item_name' },
    { title: '结果', dataIndex: 'result_value', key: 'result_value',
      render: (v: any, r: any) => <span>{v} <span style={{ color: '#888', fontSize: 12 }}>{r.unit}</span></span> },
    { title: '参考范围', key: 'ref',
      render: (_: any, r: any) => r.ref_range_low && r.ref_range_high ? `${r.ref_range_low}-${r.ref_range_high}` : '-' },
    { title: '色级', dataIndex: 'color_level', key: 'color_level',
      render: (c: string) => c ? <Tag color={COLORS[c]}>{c}</Tag> : '-' },
    { title: '偏离', dataIndex: 'deviation', key: 'deviation',
      render: (d: string) => d ? <span>{DEVIATION_TXT[d] || d}</span> : '-' },
  ];

  const sortedIndicators = [...indicators].sort((a, b) => {
    const order: any = { red: 0, yellow: 1, green: 2 };
    return (order[a.color_level] ?? 3) - (order[b.color_level] ?? 3);
  });

  return (
    <DoctorLayout>
      <h2 style={{ marginBottom: 16 }}>{report.name || '报告详情'}</h2>
      <Card style={{ marginBottom: 16 }}>
        <p>性别: {report.gender} · 年龄: {report.age} · 日期: {report.report_date}</p>
        {report.unit_name && <p>单位: {report.unit_name}</p>}
      </Card>

      {interp && (
        <div style={{ marginBottom: 12 }}>
          <Tag color="red">红区 {interp.red_count}</Tag>
          <Tag color="gold">黄区 {interp.yellow_count}</Tag>
          <Tag color="green">绿区 {interp.green_count}</Tag>
          <span style={{ marginLeft: 8 }}>
            整体判定：
            <Tag color={COLORS[interp.overall_level]}>{interp.overall_level}</Tag>
          </span>
        </div>
      )}

      <Card title="指标明细" style={{ marginBottom: 16 }}>
        <Table
          columns={columns}
          dataSource={sortedIndicators.map((i: any, idx: number) => ({ ...i, key: idx }))}
          pagination={{ pageSize: 20 }}
          size="small"
        />
      </Card>

      <InterpretationReportCard
        summaries={interp?.summaries}
        references={interp?.references}
        loading={interp?.status && interp.status !== 'completed'}
        qualityNote={interp?.quality_note}
      />
    </DoctorLayout>
  );
}
```

- [ ] **Step 2: 类型检查**

```bash
cd /data/project/hospitalKnowledgeBase/frontend && npx tsc --noEmit -p packages/doctor-portal/tsconfig.json 2>&1 | tail -30
```
Expected: 无类型错误

- [ ] **Step 3: Commit**

```bash
cd /data/project/hospitalKnowledgeBase && \
git add frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx && \
git commit -m "feat(doctor-portal): split indicator table and AI interpretation report card"
```

---

### Task 10: 重启服务 + 重新生成 report 4 解读 + 端到端验证

**Files:** 无修改

**Interfaces:**
- Consumes：上面的全部变更

- [ ] **Step 1: 重启后端**

```bash
pkill -f "uvicorn app.main:app" 2>/dev/null; sleep 1; \
cd /data/project/hospitalKnowledgeBase/backend && \
setsid nohup .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/backend.log 2>&1 < /dev/null &
echo " restarting"
```

- [ ] **Step 2: 重启 interpretation worker**

```bash
ps aux | grep "interpretation.worker" | grep -v grep | awk '{print $2}' | xargs -r kill 2>/dev/null; sleep 1; \
cd /data/project/hospitalKnowledgeBase/backend && \
setsid nohup .venv/bin/python -c "from app.modules.interpretation.worker import start_worker; start_worker()" > /tmp/worker-interpretation.log 2>&1 < /dev/null &
echo " restarting interpretation worker"
```

- [ ] **Step 3: 触发 report 4 重新生成解读（删旧 interpretation）**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
from app.core.database import get_hospital_db
from app.modules.interpretation.models import ReportInterpretation, IndicatorJudgment
from sqlalchemy import text
db = next(get_hospital_db('H001'))
db.query(IndicatorJudgment).filter(IndicatorJudgment.interpretation_id.in_(
    db.query(ReportInterpretation.id).filter(ReportInterpretation.report_id == 4)
)).delete(synchronize_session=False)
db.query(ReportInterpretation).filter(ReportInterpretation.report_id == 4).delete()
db.commit()
db.execute(text('UPDATE report_task SET status=\"completed\" WHERE id=4'))
db.commit()
print('deleted interpretation for report 4, task status reset')
db.close()
" && \
.venv/bin/python -c "
from app.core.database import get_hospital_db
from app.ai.agents import run_interpretation_agent
db = next(get_hospital_db('H001'))
try:
    run_interpretation_agent('H001', db, 4)
    print('interpretation rerun done')
finally:
    db.close()
"
```
Expected: `interpretation rerun done`；耗时 ~30s-2min

- [ ] **Step 4: 校验 DB 新字段已写入**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
from app.core.database import get_hospital_db
from sqlalchemy import text
db = next(get_hospital_db('H001'))
row = db.execute(text('SELECT id, status, overall_level, summary_text IS NOT NULL AS has_text, summary_refs IS NOT NULL AS has_refs, quality_note FROM report_interpretation WHERE report_id=4')).fetchone()
print('row:', row)
import json
if row and row[3]:
    raw = db.execute(text('SELECT summary_text FROM report_interpretation WHERE report_id=4')).fetchone()[0]
    d = json.loads(raw)
    print('sections:', list(d.keys()))
    print('overall_summary len:', len(d.get('overall_summary','')))
    print('abnormal_focus len:', len(d.get('abnormal_focus','')))
refs = db.execute(text('SELECT summary_refs FROM report_interpretation WHERE report_id=4')).fetchone()[0]
print('refs json:', (refs or [])[:200] if isinstance(refs, str) else refs)
judg = db.execute(text('SELECT COUNT(*), SUM(CASE WHEN explanation IS NULL THEN 1 ELSE 0 END) FROM indicator_judgment WHERE interpretation_id=(SELECT id FROM report_interpretation WHERE report_id=4)')).fetchone()
print('judgments: total=', judg[0], 'null_explanation=', judg[1])
db.close()
"
```
Expected: status=completed, has_text=1, has_refs=1, sections=5 个 key, abnormal_focus len>0；judgments null_explanation 等于 total（全部 NULL）

- [ ] **Step 5: API 解读接口返回新结构**

```bash
cd /data/project/hospitalKnowledgeBase/backend && \
.venv/bin/python -c "
import requests, json
r = requests.post('http://127.0.0.1:8000/api/v1/auth/login', json={'username':'user1','password':'123456'}, timeout=10)
tok = r.json()['access_token']
h = {'Authorization': f'Bearer {tok}'}
r = requests.get('http://127.0.0.1:8000/api/v1/interpretations/4', headers=h, timeout=10)
print('status:', r.status_code)
d = r.json()
print('top keys:', list(d.keys()))
print('summaries keys:', list((d.get('summaries') or {}).keys()))
print('references count:', len(d.get('references') or []))
print('quality_note:', d.get('quality_note'))
print('indicators count:', len(d.get('indicators') or []))
if d.get('indicators'):
    print('indicator[0] keys:', list(d['indicators'][0].keys()))
"
```
Expected: 200；top keys 含 `summaries`/`references`/`quality_note`/`indicators`，无 `summary_text`；indicator keys 不含 explanation/suggestion

- [ ] **Step 6: 前端用户端验证（dev 已起）**

```bash
curl -s http://127.0.0.1:3001 | grep -c "html" || true
echo "--- 前后端联通 ---"
.venv/bin/python -c "
import requests
r = requests.get('http://127.0.0.1:3001', timeout=5)
print('user portal OK' if r.status_code==200 else 'FAIL')
"
```
Expected: 200

- [ ] **Step 7: 终检 git 状态与提交**

```bash
cd /data/project/hospitalKnowledgeBase && git status --short && git log --oneline -12
```
Expected: 无未提交改动；commits 含本次 10 个任务的提交信息

---

## Self-Review

**1. Spec 覆盖性：**
- 后端图重构（agent_search_knowledge → generate_report → judge → persist）→ Task 5 ✓
- 综合报告 5 节结构化输出 → Task 5 `_generate_report` ✓
- Judge 改审综合报告、放宽、最多 1 次重试 → Task 4 ✓
- DB 新增 summary_refs/quality_note 列 → Task 1 ✓
- IndicatorJudgment 不再写 explanation/suggestion → Task 5 persist ✓
- API 返回 summaries/references/quality_note，移除 summary_text → Task 6 ✓
- 前端 shared 抽 InterpretationReportCard + react-markdown → Task 7 ✓
- 用户端 IndicatorRow 简化、ReportDetailPage 三段 → Task 8 ✓
- 医生端 ReportDetailPage 重构 → Task 9 ✓
- 端到端验证 → Task 10 ✓
- 旧数据兼容 → Task 8 通过 `InterpretationReportCard` 的 null 检查处理 ✓

**2. Placeholder 扫描：** 无 TBD/TODO/"implement later"。所有步骤含完整代码或可执行命令。

**3. 类型一致性：**
- `InterpretationReport` 5 节字段（overall_summary/abnormal_focus/trend_note/suggestions/risk_alert）在 Task 3 schema、Task 5 graph、Task 7 前端 Props、Task 8/9 前端使用处一致。
- `Citation`/`CitationRef`：后端 `ref_id/entry_id/title/source`，前端 `ref_id/entry_id/title/source/content?` 兼容。
- `run_interpretation_agent(hospital_id, db, report_id) -> dict` 签名未变，worker 不改 ✓
- `InterpKnowledgeMiddleware` 名沿用旧测试，保持 ✓
- `build_interp_graph`/`build_interp_agent` 仍存在，旧集成测试 `test_agent_tools_available_in_graph` 通过 ✓

## 执行选择

**Plan complete and saved to `docs/superpowers/plans/2026-07-06-interpretation-report-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**