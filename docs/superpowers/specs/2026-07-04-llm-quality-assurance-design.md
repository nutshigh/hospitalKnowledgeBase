# LLM 回答质量与可信度保障

## 概述

为报告解读 Agent 和聊天 Agent 增加四项质量保障能力：
1. **结构化输出** — 报告扩展 InterpBatchItem schema，聊天流式文本+尾随元数据事件
2. **引用标注** — 内联 `[n]` 标记 + 引用列表（citations），结论可追溯到知识条目
3. **确定性分级** — definite / probable / refused 三级，不确定的拒绝回答
4. **LLM as a Judge** — 仅报告阶段，独立 Agent 审核可追溯性与编造，不通过则回滚重试（最多 2 次）

## 适用范围

| 能力 | 报告解读 | 聊天 |
|------|---------|------|
| 结构化输出 | ✅ 扩展 InterpBatchItem | ✅ 流式文本 + 尾随 structured 事件 |
| 引用标注 | ✅ 内联 [n] + citations | ✅ 内联 [n] + citations |
| 确定性分级 | ✅ 每个指标项 | ✅ 整条回复 |
| LLM Judge | ✅ 独立 Agent 审核 | ❌ 不做 |

## 现有代码基础

### 报告解读（interp_graph.py）

- LangGraph 线性流程：`load_indicators → run_rules → filter_abnormal → agent_batch → persist → END`
- `agent_batch` 用 `create_agent` + `ToolStrategy(InterpBatchResult)` 生成结构化输出
- 现有 `InterpBatchItem`：`indicator_id / explanation / suggestion / knowledge_ref_ids(int列表)`
- `InterpKnowledgeMiddleware` 拦截 search_knowledge 累积 `{entry_id: {entry_id, title}}`
- `persist` 写 `IndicatorJudgment(explanation, suggestion, knowledge_refs=JSON)`

### 聊天（chat_graph.py）

- SSE 流式：`token` 事件逐字输出 + `done` 事件
- 无结构化输出，自由文本 AIMessage
- `KnowledgeRefsMiddleware` 累积 `{entry_id, title}`（跳过 KG 结果）
- `ChatMessage.knowledge_refs`（JSON）存引用列表

### search_knowledge 工具

返回 `[{entry_id, title, content, score, source}]`，`source` 为 `"document"` 或 `"knowledge_graph"`。

## 架构

### 报告解读流程（改造后）

```
load_indicators → run_rules → filter_abnormal → agent_batch → judge
                                                  ↑              |
                                                  |── (fail, retry<2) ──|
                                                  |
                                          (fail, retry>=2) → error_handler → END
                                                  |
                                          (pass) → persist → END
```

### 聊天流程（改造后）

```
用户消息 → agent.astream_events（流式 token，含 [n] 标记）
         → 流结束 → 轻量 LLM 调用（certainty 分类 + citations 提取）
         → yield structured 事件
         → yield done 事件
         → 入库（content + knowledge_refs 含 citations）
```

## 组件设计

### 1. 引用标注数据结构

```python
class Citation(BaseModel):
    ref_id: int                 # 内联标记编号 [1], [2]...
    entry_id: Optional[int]     # 知识条目 ID（KG 结果为 None）
    title: str
    source: str                 # "document" | "knowledge_graph"
```

替代原有的 `knowledge_ref_ids: list[int]`。持久化时写入 `knowledge_refs` JSON 列（兼容旧格式：旧数据是 `[{entry_id, title}]`，新数据是 `[{ref_id, entry_id, title, source}]`）。

### 2. 确定性分级

| 级别 | 含义 | 示例 |
|------|------|------|
| `definite` | 基于明确指标数值 + 参考范围的直接判断 | "血糖 8.5 高于参考 3.9-6.1，属于偏高" |
| `probable` | 基于知识库推理，非直接数值判断 | "可能与代谢综合征相关，建议进一步检查" |
| `refused` | 信息不足或超出能力范围 | "该指标需要结合更多检查结果才能判断" |

System Prompt 增加确定性规则：
- 指标数值与参考范围直接对比的结论 → definite
- 基于知识库推理、需要专业判断的结论 → probable
- 信息不足、超出助手能力、需要医生诊断的 → refused

### 3. 报告解读 InterpBatchItem 扩展

```python
class InterpBatchItem(BaseModel):
    indicator_id: int
    explanation: str              # 含内联 [n] 标记
    suggestion: str               # 含内联 [n] 标记
    certainty: str                # "definite" | "probable" | "refused"
    certainty_reason: str         # 确定性判定理由
    citations: list[Citation]     # 引用列表
```

`InterpBatchResult` 不变（`items: list[InterpBatchItem]`）。

### 4. System Prompt 增强（报告解读）

在现有 `INTERP_SYSTEM_PROMPT` 基础上增加：

```
引用标注规则：
1. 在 explanation 和 suggestion 中，每个结论性陈述后用 [n] 标注来源
2. [n] 对应 citations 列表中的 ref_id
3. citations 中每项需关联到 search_knowledge 返回的 entry_id 和 title
4. 来自知识图谱的结果 entry_id 为 null，source 为 "knowledge_graph"

确定性分级规则：
- definite：基于指标数值与参考范围的直接对比判断
- probable：基于知识库推理但非直接数值判断
- refused：信息不足或超出助手能力范围，不做猜测

输出要求：
- explanation 和 suggestion 中必须用 [n] 标注所有结论性陈述的来源
- 没有引用支撑的结论性陈述视为编造，禁止输出
- certainty 级别必须与结论性质匹配
```

### 5. Judge Agent（仅报告阶段）

#### 5.1 Judge Agent 定义

新建 `app/ai/agents/judge_graph.py`：

```python
JUDGE_SYSTEM_PROMPT = """你是体检报告解读质量审核员。你的职责是审查 AI 生成的解读报告，判断质量是否合格。

审核标准：
1. 可追溯性：explanation 和 suggestion 中的每个结论性陈述是否有对应的 [n] 标记，且该 [n] 在 citations 列表中有对应条目
2. 编造检测：是否存在没有引用支撑的结论性陈述（有结论但无 [n] 标记，或 [n] 在 citations 中找不到）
3. 确定性合理性：certainty 级别是否与结论性质匹配
   - definite 应仅用于基于明确数值的判断
   - probable 用于推理性结论
   - refused 用于信息不足的情况

判断结果：
- passed=true：所有结论可追溯，无编造，确定性合理
- passed=false：列出具体问题（哪条指标的哪个结论缺少引用/编造/确定性不合理），给出改进建议
"""

class JudgeResult(BaseModel):
    passed: bool
    issues: list[str]          # 具体问题列表，如 "指标ID:5 的 explanation 中'血压偏高'缺少引用标注"
    suggestions: str           # 改进建议，回传给生成 Agent
```

Judge Agent 用 MedGo（同 `get_chat_model`），但独立 system prompt 和角色。无工具调用（纯文本审查），用 `ToolStrategy(JudgeResult)` 输出结构化结果。

#### 5.2 Judge 节点

```python
def judge(state: InterpState) -> dict:
    """审查 agent_batch 的输出，返回 JudgeResult。"""
    agent = build_judge_agent()
    # 构造审查输入：把 agent_explanations + knowledge_refs 格式化为审查文本
    review_text = _format_for_review(state)
    result = agent.invoke(
        {"messages": [HumanMessage(content=review_text)]},
        config={"recursion_limit": settings.AGENT_MAX_ITERATIONS * 2},
    )
    judge_result = result.get("structured_response")
    return {"judge_result": judge_result.dict() if judge_result else {"passed": True, "issues": [], "suggestions": ""}}
```

#### 5.3 条件边

```python
def after_judge(state: InterpState) -> str:
    if state["judge_result"]["passed"]:
        return "persist"
    if state["judge_retry_count"] >= settings.JUDGE_MAX_RETRIES:
        return "error_handler"
    return "agent_batch"  # 回滚重试
```

#### 5.4 agent_batch 重试逻辑

`_agent_batch` 检查 `state["judge_retry_count"]`：
- 首次（count=0）：正常 user message
- 重试（count>0）：在 user message 末尾追加 judge 的 issues + suggestions

```python
def _agent_batch(state, build_agent_fn, db):
    ...
    user_content = f"""以下是本报告的异常指标...{indicators_text}..."""
    
    # 重试时追加 judge 反馈
    retry_count = state.get("judge_retry_count", 0)
    if retry_count > 0:
        judge_result = state.get("judge_result", {})
        issues = judge_result.get("issues", [])
        suggestions = judge_result.get("suggestions", "")
        user_content += f"""

## 质量审核反馈（第 {retry_count} 次重试）
上次生成存在以下问题：
{chr(10).join(f'- {issue}' for issue in issues)}

改进要求：
{suggestions}

请修正以上问题，重新生成解读结果。"""
    
    result = agent.invoke(...)
    return {"agent_explanations": ..., "knowledge_refs": ..., "judge_retry_count": retry_count + 1}
```

#### 5.5 error_handler 节点

```python
def error_handler(state: InterpState) -> dict:
    """Judge 未通过且重试次数用尽，标记失败，留待人工处理。"""
    from app.modules.interpretation.models import ReportInterpretation
    
    interp = db.query(ReportInterpretation).filter(
        ReportInterpretation.report_id == state["report_id"],
        ReportInterpretation.status == "processing",
    ).first()
    if interp:
        interp.retry_count += 1
        interp.status = "failed"
        interp.summary_text = f"Judge 审核未通过（重试 {state['judge_retry_count']} 次）: " + \
                              "; ".join(state["judge_result"].get("issues", []))
        db.commit()
    logger.warning("Report %s judge failed after %d retries, needs manual review",
                   state["report_id"], state["judge_retry_count"])
    return {}
```

#### 5.6 InterpState 扩展

```python
class InterpState(TypedDict):
    ...现有字段...
    judge_result: dict          # {passed, issues, suggestions}
    judge_retry_count: int      # 0, 1, 2
```

#### 5.7 LangGraph 编排改造

```python
g = StateGraph(InterpState)
g.add_node("load_indicators", load_indicators)
g.add_node("run_rules", run_rules)
g.add_node("filter_abnormal", filter_abnormal)
g.add_node("agent_batch", agent_batch)
g.add_node("judge", judge)                    # 新增
g.add_node("error_handler", error_handler)     # 新增
g.add_node("persist", persist)

g.set_entry_point("load_indicators")
g.add_edge("load_indicators", "run_rules")
g.add_edge("run_rules", "filter_abnormal")
g.add_edge("filter_abnormal", "agent_batch")
g.add_edge("agent_batch", "judge")             # agent_batch → judge
g.add_conditional_edges("judge", after_judge, {  # 条件边
    "persist": "persist",
    "agent_batch": "agent_batch",
    "error_handler": "error_handler",
})
g.add_edge("persist", END)
g.add_edge("error_handler", END)
```

#### 5.8 persist 适配

`_map_structured_to_explanations` 改为使用新的 `citations` 字段（取代 `knowledge_ref_ids`）：

```python
for item in structured.items:
    explanations[item.indicator_id] = {
        "explanation": strip_think_tags(item.explanation),
        "suggestion": strip_think_tags(item.suggestion),
        "certainty": item.certainty,
        "certainty_reason": item.certainty_reason,
    }
    # citations 直接从结构化输出取，无需从 knowledge_results 反查
    mapped_refs[item.indicator_id] = [
        {"ref_id": c.ref_id, "entry_id": c.entry_id, "title": c.title, "source": c.source}
        for c in item.citations
    ]
```

`IndicatorJudgment` 持久化时新增 `certainty` 和 `certainty_reason` 字段写入（需扩展 model 或复用现有字段）。

### 6. 聊天 Agent 改造

#### 6.1 System Prompt 增强

在现有 `CHAT_SYSTEM_PROMPT` 基础上增加引用标注和确定性规则（同报告解读的规则段落）。

#### 6.2 流式输出 + 尾随元数据

`run_chat_agent` 流程不变（流式 token 输出），在流结束后增加轻量 LLM 调用：

```python
async def run_chat_agent(...):
    ...现有流式逻辑...
    # 流结束后，构造 structured 元数据
    refs = (final_state or {}).get("knowledge_refs", [])
    
    # 轻量 LLM 调用：对回复做确定性分类 + 提取 citations
    structured_data = await _extract_structured_metadata(final_response, refs)
    
    yield {"event": "structured", "data": structured_data}
    yield {"event": "done", "data": {"message_id": msg.id}}
```

`_extract_structured_metadata` 用 MedGo 做一次非流式调用，输入为回复文本 + knowledge_refs，输出为：
```python
class ChatStructuredResult(BaseModel):
    certainty: str              # "definite" | "probable" | "refused"
    certainty_reason: str
    citations: list[Citation]   # 从 refs 和回复文本中的 [n] 标记提取
```

#### 6.3 入库

`ChatMessage.knowledge_refs` 扩展为含 `ref_id/entry_id/title/source` 的完整引用列表。

### 7. 配置

```python
# app/config.py
JUDGE_MAX_RETRIES: int = 2       # Judge 审核不通过的最大重试次数
```

### 8. 数据模型扩展

#### IndicatorJudgment

新增两列（或复用 summary_text 存 certainty）：
```sql
ALTER TABLE indicator_judgment ADD COLUMN certainty VARCHAR(10) DEFAULT NULL;
ALTER TABLE indicator_judgment ADD COLUMN certainty_reason TEXT DEFAULT NULL;
```

`knowledge_refs` JSON 结构升级（向后兼容）：
```json
// 新格式
[{"ref_id": 1, "entry_id": 5, "title": "血糖偏高解读", "source": "document"}]
// 旧格式（兼容）
[{"entry_id": 5, "title": "血糖偏高解读"}]
```

#### ChatMessage

`knowledge_refs` JSON 同样升级为新格式（向后兼容）。

## 数据流

### 报告解读（含 Judge 回滚）

```
load_indicators → run_rules → filter_abnormal
    → agent_batch (生成 explanation+suggestion+certainty+citations)
    → judge (审核可追溯性+编造+确定性)
        ├── passed → persist (写库) → END
        ├── fail, retry<2 → agent_batch (追加 judge feedback 重试)
        └── fail, retry>=2 → error_handler (status=failed, 留人工) → END
```

### 聊天

```
用户消息 → 流式 token (含 [n] 标记) → 流结束
    → 轻量 LLM 调用 (certainty + citations 提取)
    → yield structured 事件
    → yield done 事件
    → 入库 (content + knowledge_refs 含 citations)
```

## 降级策略

| 场景 | 行为 |
|------|------|
| Judge Agent 调用失败 | 视为 passed=true（不阻塞流程） |
| 聊天 structured 元数据提取失败 | certainty=probable, citations=[] |
| LLM 未输出 [n] 标记 | citations 为空，Judge 会标记为问题 |

## 不改动

- RAG 检索流程（vector+BM25+KG 分通道）不变
- 知识库导入/索引不变
- search_knowledge 工具接口不变
- MedGo / BGE-M3 / PaddleOCR / Reranker 不变
- 前端（后端先发 structured 事件，前端后续对接）
