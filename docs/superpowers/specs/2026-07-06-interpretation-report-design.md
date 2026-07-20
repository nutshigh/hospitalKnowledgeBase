# 综合性 AI 解读报告（结构化分节）— 设计文档

- 日期：2026-07-06
- 状态：草案 / 待评审
- 作者：基于与人类用户讨论确认

## 1. 背景与目标

### 1.1 现状问题

当前解读模块（`backend/app/ai/agents/interp_graph.py`）的输出形态为「按指标生成 explanation + suggestion」，前端（`frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`）把解释内嵌到每个指标行的展开区。这导致：

- 视图碎片化，缺乏一份整体叙事性的「AI 解读报告」。
- `report_interpretation.summary_text` 列从未被填充（仅 schema 留位）。
- Judge 审核仍按「每指标 explanation」结构运行，日志显示 `Report ... judge failed after 2 retries`，多轮重试拖慢生成（从 report 4 实测约 5 分钟）。

### 1.2 目标形态

用户端报告详情页改为三段式：

1. **报告信息卡** + **三色统计条**（保留现状）
2. **指标区**：纯指标列表（item_name / 结果 / 单位 / 参考范围 / 色标），不展开、无 inline explanation
3. **AI 解读报告卡**：一份独立的、结构化分节的综合报告，由 LLM 基于「已分级指标 + RAG + KG」一次合成

医生端同步为「指标表 + 综合报告」二段。

### 1.3 非目标

- 不重做解析（OCR/VLM）流程
- 不改 RAG/KG 检索底层
- 不实现「重新生成解读」按钮（本期不做）
- 不改历史解读记录的回填（旧数据兼容退化）

## 2. 设计决策

| 决策点 | 决定 |
|--------|------|
| 综合报告形态 | **结构化分节**（固定 5 节框架） |
| 指标级 explanation/suggestion | **全部并入综合报告，不单独保存** |
| Judge 流程 | **保留，但放宽**：审综合报告，最多重试 1 次；不通过仍持久化并附 quality_note |
| 引用标注 | **保留** [n] 内联 + 文末参考来源列表 |
| 数据存储 | `summary_text` 存分节 JSON；新增 `summary_refs` JSON 列存引用 |

## 3. 综合报告结构（结构化分节）

固定 5 节，每节为 Markdown 文本：

| 节字段 | 说明 |
|--------|------|
| `overall_summary` | 整体健康评估（1-2 段） |
| `abnormal_focus` | 重点异常指标解读（每红/黄区指标一小节，含 [n] 引用） |
| `trend_note` | 历年趋势研判（首份报告说明） |
| `suggestions` | 具体可执行建议 |
| `risk_alert` | 风险提示 / 复查就医建议（红区指标必有） |

后端 Pydantic schema：

```python
class InterpretationReport(BaseModel):
    overall_summary: str
    abnormal_focus: str
    trend_note: str
    suggestions: str
    risk_alert: str

class InterpretationReportWithRefs(BaseModel):
    report: InterpretationReport
    references: list[Citation]
```

输出 JSON：

```json
{
  "summaries": {
    "overall_summary": "...",
    "abnormal_focus": "...[1]...[2]...",
    "trend_note": "...",
    "suggestions": "...[1]...",
    "risk_alert": "..."
  },
  "references": [
    {"ref_id": 1, "entry_id": 12, "title": "...", "source": "document", "content": "..."},
    {"ref_id": 2, "entry_id": null, "title": "高血糖 → KG", "source": "knowledge_graph"}
  ]
}
```

## 4. 解读图重构

### 4.1 新 State 字段

```python
class InterpState(TypedDict):
    hospital_id: str
    report_id: int
    user_id: int                 # 新增（用于趋势查询）
    indicators: List[dict]
    judgments: List[dict]
    abnormal_indicators: List[dict]
    knowledge_results: dict      # Agent 累积的 RAG/KG 来源
    overall_level: str
    red_count: int
    yellow_count: int
    green_count: int
    report: InterpretationReport  # 新增
    references: list             # 新增（合并所有引用）
    judge_result: dict
    judge_retry_count: int
```

### 4.2 节点变更

| 节点 | 变更 |
|------|------|
| `load_indicators` | 同现状，补充查 `report_info.user_id` |
| `run_rules` | 不变 |
| `filter_abnormal` | 不变 |
| `agent_search_knowledge` | **替换 `agent_batch`**。LLM Agent 一次性查所有异常指标的医学知识，累积 `knowledge_results`（沿用 `InterpKnowledgeMiddleware` 累积机制）。返回 dict 直接接到 generate_report。 |
| `generate_report` | **新节点**。单次 LLM 调用，输入 = 异常指标 + 知识库结果 + 红/黄/绿计数 + 历年趋势（拉取），输出结构化 `InterpretationReport`。后置 `inject_citations` 注入 [n] 合并 references。 |
| `judge` | **改造**。审对象改为综合报告（5 节 markdown + 引用）。prompt 放宽，标准改为「主要结论有引用 / 无明显编造 / 结构完整」。最多 1 次重试 |
| `after_judge` | 条件边：通过 → persist；不通过且 retry<1 → generate_report；否则 → persist（带 quality_note） |
| `error_handler` | 删除（每条路径最终都会 persist） |
| `persist` | `summary_text = json.dumps(report.dict())`；`summary_refs = references`；新增字段。`IndicatorJudgment` 仅写 deviation/color_level/matched_rule_id，删去 explanation/suggestion/knowledge_refs/certainty 写入（列保留 schema 不删） |

### 4.3 generate_report 节点细节

伪代码：

```python
def generate_report(state, db):
    abnormal = state["abnormal_indicators"]
    knowledge = list(state["knowledge_results"].values())
    trend = _fetch_trend(state["user_id"], db)

    # 1. 构造 LLM 输入：异常指标列表 + 知识库 chunks + 红/黄/绿计数 + 趋势
    # 2. LLM 调用 with ToolStrategy(InterpretationReport)
    # 3. inject_citations 注入 [n]，每节单独注入并合并 references
    refs_all = []
    summaries = {}
    for field in ["overall_summary", "abnormal_focus", "trend_note", "suggestions", "risk_alert"]:
        annotated, citations = inject_citations(getattr(raw, field), knowledge)
        summaries[field] = annotated
        refs_all = _merge_citations(refs_all, citations)

    return {"report": InterpretationReport(**summaries), "references": refs_all}
```

### 4.4 Agent 拆分

- `agent_search_knowledge` 仍用 `create_agent` + `search_knowledge`/`get_triage_rules` 工具，但 prompt 改为「一次性查全所有异常指标的知识，不要急着生成解读」；不要求结构化输出。
- `generate_report` 不作为 Agent，直接 `model.invoke` + `ToolStrategy(InterpretationReport)`。
- 这把「决定查什么」与「如何生成报告」解耦，避免单 agent 思维过长导致递归/超时。

## 5. 数据库 schema 变更

### 5.1 `report_interpretation` 表

- 复用 `summary_text`（存 `InterpretationReport.dict()` 的 JSON 字符串）
- 新增 `summary_refs` JSON 列（NULL 兼容旧数据）
- 新增 `quality_note` VARCHAR(255) NULL（Judge 未通过时存提示，前端可选展示）

迁移脚本（MySQL）：

```sql
ALTER TABLE report_interpretation
  ADD COLUMN summary_refs JSON NULL AFTER summary_text,
  ADD COLUMN quality_note VARCHAR(255) NULL AFTER summary_refs;
```

### 5.2 `indicator_judgment` 表

Schema 不变；service 不再写入 `explanation/suggestion/knowledge_refs/certainty/certainty_reason`（保留列以兼容旧数据，新解读一律写 NULL）。

## 6. API 变更

### 6.1 `GET /api/v1/interpretations/{report_id}`

新响应：

```json
{
  "id": 7, "report_id": 4,
  "overall_level": "yellow",
  "red_count": 0, "yellow_count": 5, "green_count": 33,
  "status": "completed",
  "summaries": {
    "overall_summary": "...",
    "abnormal_focus": "...[1]...[2]...",
    "trend_note": "...",
    "suggestions": "...",
    "risk_alert": "..."
  },
  "references": [
    {"ref_id": 1, "entry_id": 12, "title": "...", "source": "document"}
  ],
  "quality_note": null,
  "indicators": [
    {"indicator_id": 88, "item_name": "ALT", "result_value": "62",
     "unit": "U/L", "deviation": "high", "color_level": "yellow"}
  ],
  "created_at": "...",
  "completed_at": "..."
}
```

- 不再返回 `summary_text`（保留列兼容旧前端兜底，但在响应中省略）
- `indicators` 仍含 `deviation`/`color_level`（前端渲染色标），但**不再含 explanation/suggestion**

### 6.2 Pydantic schema

```python
class IndicatorJudgmentSchema(BaseModel):
    indicator_id: int
    item_name: str
    result_value: Optional[str] = None
    unit: Optional[str] = None
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
    red_count: int; yellow_count: int; green_count: int
    status: str
    summaries: InterpretationReportSchema
    references: list[CitationSchema] = []
    quality_note: Optional[str] = None
    indicators: list[IndicatorJudgmentSchema] = []
    created_at: datetime
    completed_at: Optional[datetime] = None
```

旧字段 `summary_text` 不在 `InterpretationResponse` 中导出（保留数据库列用于兼容）。

### 6.3 前端响应兼容

旧前端读了 `interpretation.summary_text` 和 `indicators[].explanation`，新响应未提供。结果：旧前端 InfoCard 的 summary 块隐藏（已检查为 conditional），指标行 explanation 展开块隐藏（已检查为 conditional）。不会崩，只是退化。

## 7. 前端（用户端）

### 7.1 路由

`ReportDetailPage.tsx`：
- 保持 `Promise.all([get report, get interpretation])`
- 主体三段：信息卡 + 指标区 + AI 解读报告卡
- `interpretation?.status !== 'completed'` → 显示「AI 解读生成中…」骨架

### 7.2 指标区

- 复用 `IndicatorRow` 但**移除 expanded/explanation props**
- 改造 `IndicatorRow`：默认无展开，去掉箭头转向动画；解释展开区删除
- 排序：红 → 黄 → 绿（可选，按 `color_level` 排序异常优先）
- 列表过长可考虑分组小标题（本期可选）

### 7.3 AI 解读报告卡（新组件 `InterpretationReportCard.tsx`）

Props:

```typescript
interface Props {
  summaries: {
    overall_summary: string;
    abnormal_focus: string;
    trend_note: string;
    suggestions: string;
    risk_alert: string;
  };
  references: Array<{
    ref_id: number; entry_id: number | null; title: string; source: string; content?: string;
  }>;
  loading?: boolean;
  qualityNote?: string | null;
}
```

渲染：
- 5 个分节卡片，每张卡片：固定中文标题（整体评估 / 重点异常解读 / 历年趋势 / 健康建议 / 风险提示）+ Markdown 正文
- `risk_alert` 卡片若非空 + overall_level=red 用红底强调
- 底部「参考来源」可折叠列表，每项显示 `[n] title`，点击 [n] 跳转/弹窗显示完整 content（复用 `ChatBubble` 的 popover 模式）
- `qualityNote` 若非 null，顶部一条 Alert 黄底小字「AI 解读质量审核建议：…」
- `loading=true` 显示骨架/Spin

### 7.4 依赖

`packages/shared` 安装一次 `react-markdown` + `remark-gfm`，三个 portal 复用。

## 8. 前端（医生端）

`doctor-portal/pages/ReportDetailPage.tsx` 重构同步：
- 顶部 InfoCard
- 指标表（用 antd Table，列：指标名 / 结果 / 单位 / 参考范围 / 色标 / 偏离方向）
- 下方综合报告区（复用 `InterpretationReportCard`，从 shared 导出）
- 删除「每个指标卡片 + explanation」循环

## 9. 共享组件抽取

`InterpretationReportCard` 放 `packages/shared/src/components/InterpretationReport/`，导出：

```
shared/src/components/InterpretationReport/InterpretationReportCard.tsx
shared/src/components/InterpretationReport/MarkdownRenderer.tsx
shared/src/components/InterpretationReport/CitationPopover.tsx
shared/src/index.ts  ← re-export
```

`MarkdownRenderer` 包装 `react-markdown` + `remark-gfm`，统一样式。

## 10. 受影响文件清单

### 后端
- `backend/app/modules/interpretation/models.py` — 新增列
- `backend/app/modules/interpretation/schemas.py` — 新 schema
- `backend/app/modules/interpretation/router.py` — 响应改成 summaries + references
- `backend/app/modules/interpretation/service.py` — 持久化新字段，返回新结构（如已有 get_interpretation/get_judgments 需改造）
- `backend/app/ai/agents/interp_graph.py` — 重构图：新增 generate_report、改造 judge、persist
- `backend/app/ai/agents/judge_graph.py` — prompt 改审综合报告，逻辑放宽
- `backend/app/ai/agents/__init__.py` — export 不变（run_interpretation_agent 仍对外）
- `backend/scripts/` 或 Alembic — 迁移脚本 SQL（手动 v1）

### 前端
- `shared/package.json` — 加 `react-markdown`、`remark-gfm`
- `shared/src/components/InterpretationReport/*` — 新建
- `shared/src/index.ts` — re-export
- `user-portal/src/pages/ReportDetailPage.tsx` — 重构三段
- `user-portal/src/components/IndicatorRow.tsx` — 简化（移除 expanded/explanation）
- `doctor-portal/src/pages/ReportDetailPage.tsx` — 重构
- `shared` Markdown 渲染组件

### 不变更
- report 模块（解析）不受影响
- chat 模块、ChatBubble.tsx 不受影响（独立功能）
- worker 入口（`interpretation/worker.py`）不受影响

## 11. 测试与验收

### 11.1 单元

- `interp_graph._map_structured_to_explanations` → 替换为 `_map_report_to_state` 测试（注入 [n]、合并 references）
- `judge_graph._format_for_review` 重写测试（审综合报告格式）
- service 层 `get_interpretation` 返回新结构

### 11.2 集成

- 上传一份已有报告 → 等解读完成 → `GET /interpretations/{id}` → 5 节齐全 + references 非空
- `IndicatorJudgment` 行的 `explanation/suggestion` 为 NULL
- Judge 不通过时仍 persist（quality_note 非空，status=completed）

### 11.3 前端

- 用户端报告详情页：指标区无展开，下方综合报告卡 5 节齐全
- 医生端报告详情页同步展示
- 旧报告（id 1/2/3）打开不崩，仅显示指标 + 「暂无 AI 解读报告」

## 12. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 综合报告单次 LLM 输出过长截断 | 控制每节 input 上下文（abnormal_focus 只列异常指标，建议 max_tokens=4096） |
| Judge 重试仍慢 | 限制单次重试，且 Judge 走同一 LLM 但 prompt 短 |
| 前端 react-markdown 首次引入 | shared 一次性安装，纯组件库低风险 |
| 旧解读数据不动但 UI 不展示 | 可接受退化（contact 列维护，未删） |

## 13. 迁移步骤顺序

1. DB ALTER（summary_refs, quality_note）
2. 后端 schema/models
3. 后端 interp_graph 重构 + judge_graph 改造
4. 后端 service/router
5. 单元测试
6. shared 组件抽取（react-markdown 引入）
7. 用户端 IndicatorRow 简化 + ReportDetailPage 重构
8. 医生端 ReportDetailPage 重构
9. 端到端验证：上传报告 → 等解读完成 → 前端展示

## 14. 后续可拓展（本期不做）

- 「重新生成解读」按钮（医生端）
- 解读报告导出（Word/PDF 复用 statistics 模块）
- 报告版本对比（regenerate 时保留旧版 summary_refs）