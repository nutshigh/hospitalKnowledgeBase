# AGENT-HANDOFF — 解读知识检索改为 planner/executor（消除 ReAct agent 退化循环）

日期：2026-09-13
接手对象：下一个实现该改动的 Agent
交付物：把「AI 解读」里的知识检索步骤，从**模型自驱的 ReAct 工具 agent**改为
**planner（确定性产出检索计划）+ executor（确定性执行）**，彻底消除模型退化循环。

---

## 1. 前因（真实事故）

`hospital_H004` report 31（滨州 患者「董延广」，user6）在 `2026-09-13 21:25:36` 进入
"AI 解读"，随后一直卡在 `report_interpretation.status='processing'`，前台显示"AI 解读中"。

时间线（证据来自 `/data/logs/app.log` 与 `/data/logs/vllm-medgo.stdout.log`）：

| 时间 | 事件 |
|------|------|
| 21:25:36 | `app.parse:conclusion extracted via LLM report=31 len=1334`（**总检建议提取成功**） |
| 21:25:36 | `app.interp:interpretation start report=31`（解读开始） |
| 21:25:41 | MedGo 收到「知识检索子 agent」请求（`SEARCH_SYSTEM_PROMPT`），已对 4 个指标调完 `search_knowledge`，第 4 个 `FPSA/TPSA` 返回**空**；随后这一轮生成不退化为正常收尾，**一直生成** |
| 21:35:41 | 客户端 `request_timeout=600s` 触发，OpenAI client 默认 `max_retries=2` **自动重发同一请求** |
| 21:55:43 | `openai.APITimeoutError`，`latency_ms=1806844`（≈30min = 3×600s） |
| 21:55:43 | `interp fail report=31 ... Request timed out.`；同时 RabbitMQ 报 `406 PRECONDITION_FAILED - delivery acknowledgement on channel 1 timed out (1800000 ms)` → **broker 关 channel、消息丢失** |
| 之后 | 报告停在 `status='pending'`、`retry_count=1`，**没有任何队列消息**，无人再处理 → "卡死" |

**结论**：卡住的是「AI 解读」里的**知识检索步**，不是总检建议提取（后者已成功）。
不是「指标太多」（这次只有 4 个）。

---

## 2. 现状代码：为什么会有循环

### 2.1 解读这一步是 ReAct agent，不是 planner/executor

`backend/app/ai/agents/interp_graph.py`：

- `build_interp_agent()`（:306-320）：
  `create_agent(model, tools=INTERP_TOOLS, system_prompt=SEARCH_SYSTEM_PROMPT, response_format=ToolStrategy(ConfirmSchema), middleware=[InterpKnowledgeMiddleware(), ToolCallGuardMiddleware()])`
- `agent_search_knowledge()`（:615-632）里 `asyncio.run(_guarded(agent.ainvoke({...}, config={"recursion_limit": AGENT_MAX_ITERATIONS*2}, ...)))`

这是一个**模型自驱循环**：模型决定调哪些工具/什么词 → 执行 → 观察结果 → 再决定……
**终止权在模型手里**（模型必须自己输出 `ConfirmSchema` 才算完）。提示词还主动鼓励迭代：
`SEARCH_SYSTEM_PROMPT`（:49-58）第 4 条「如果某个指标第一次没搜到好结果，换一个查询词再试一次」。

### 2.2 循环有两层

1. **agent 步循环**：上限 `recursion_limit=16`（`app/config.py:104 AGENT_MAX_ITERATIONS=8`）。
2. **单次生成内部的重复**（本次真正卡住的层）：`recursion_limit` **管不到**。vLLM 的
   tool-call guided-decoding schema 是 `{"type":"array","minItems":1, ...}`，**没有 `maxItems`**；
   加上该 agent `max_tokens=16384`、`repetition_penalty=1.0`（无重复抑制），模型可在**一步内**
   产出上万 token 的合法（但无意义）tool-call 数组。只有 HTTP `request_timeout` 能截。

### 2.3 对比：chat 问答才是 planner/executor（无循环）

`backend/app/ai/agents/chat_planner.py`：

- `chat_plan()`（:74-97）：`model.with_structured_output(ChatPlan)` **一次性**产出
  `tool_calls`（工具 + 关键词），`temperature=0.0`，**不执行工具**。
- `execute_plan()`（:218+）：`for tc in plan.tool_calls:` **单趟执行**，完事拼 context，
  **不回头再问模型**。所以没有循环。

### 2.4 关键事实：agent 其实没做任何"选词"增值

本次 MedGo 实际发出的 tool_call 参数就是指标名的**原样照抄**：

```
{"name":"search_knowledge","arguments":{"query":"尿酸"}}
{"name":"search_knowledge","arguments":{"query":"总胆固醇"}}
{"name":"search_knowledge","arguments":{"query":"血糖"}}
{"name":"search_knowledge","arguments":{"query":"FPSA/TPSA"}}
```

即：这步的"智能"约等于 0。**确定性计划完全能复现**（每个异常指标 = 一次检索），
故不需要保留 LLM planner。这是本次方案选择的主要依据。

---

## 3. 目标设计：确定性 planner + 确定性 executor

把 `agent_search_knowledge` 节点内部改为：

```
plan   = plan_knowledge_search(state["abnormal_indicators"])          # 纯函数，不调 LLM
results= execute_search_plan(state["hospital_id"], plan)             # 纯函数，只调 ai_rag.search
return {"knowledge_results": results}
```

### 3.1 Planner（确定性）

新增纯函数（建议放 `interp_graph.py`，或抽到 `app/ai/agents/knowledge_search.py`）：

```python
@dataclass
class SearchCall:
    indicator: str        # 归属指标名（用于日志/追溯）
    query: str            # 首选检索词
    fallback_queries: list[str]   # 优选无结果时依次尝试

def plan_knowledge_search(abnormal_indicators: list[dict]) -> list[SearchCall]:
    # 1) 按 item_name 去重（filter_abnormal 可能带重复，见 term_normalizer.py:512-519 的说明）
    # 2) 每个指标一条 SearchCall：query = item_name
    # 3) fallback_queries（确定性，保守）：
    #    a. term_normalizer.normalize_item_name(item_name) / resolve_canonical(item_name) 归一
    #    b. 名称含 "/" 时按 "/" 拆子项（如 FPSA/TPSA -> FPSA, TPSA）
    #    c. 去括号/尾缀（复用 term_normalizer._strip_affixes 之类，若可用）
    #    去重、去掉与 query 相同的项、上限（如每个指标 ≤3 个 fallback）
```

要点：**不引入 LLM**；计划本身可打印/可测。

### 3.2 Executor（确定性）

```python
def execute_search_plan(hospital_id: str, plan: list[SearchCall]) -> dict:
    merged = {}
    for call in plan:
        for q in [call.query, *call.fallback_queries]:
            results = ai_rag.search(hospital_id, q)   # from app.ai import rag as ai_rag
            if results:
                merged.update(_refs_dict_from_search_results(results))
                break
        else:
            logger.info("knowledge search empty indicator=%s query=%s", call.indicator, call.query)
    return merged
```

- `_refs_dict_from_search_results`：把现有的 `_extract_refs_dict_from_tool_result`
  （`interp_graph.py:127-152`）里对「结果条目 → refs dict」的逻辑抽出来，直接作用在
  `SearchResult` 对象上（`entry_id/title/content/source`）。
  **`knowledge_results` 的 key/值形状必须与现状完全一致**：
  - 文档：`{entry_id(int): {"entry_id","title","source","content"}}`
  - 知识图谱：`{"kg:<title>": {"entry_id": None, "title","source":"knowledge_graph","content"}}`

### 3.3 接线

- 把 `agent_search_knowledge` 节点体（`interp_graph.py:615-632`）替换为上两步；
  **节点名保持 `agent_search_knowledge`**（图边 :718-719 不用改），或改名并同步 `add_node/edges`。
- `state["abnormal_indicators"]` 为空时直接 `return {"knowledge_results": {}}`（保留现有短路）。

---

## 4. 需要改/删的文件与函数

| 文件 | 动作 |
|------|------|
| `backend/app/ai/agents/interp_graph.py` | 新增 `SearchCall`/`plan_knowledge_search`/`execute_search_plan`/`_refs_dict_from_search_results`；改 `agent_search_knowledge` |
| 同上 | **退役**（确认无其它引用后再删）：`build_interp_agent`(:306-320)、`SEARCH_SYSTEM_PROMPT`(:49-58)、`ConfirmSchema`(:61-62)、`InterpKnowledgeMiddleware`(:155-181)、`ToolCallGuardMiddleware`(:260-303)、`_guard_sanitize_tool_calls`(:194-216)、`_guard_raw_tool_calls_bad`(:219-231)、`_guard_sanitize_message`(:234-257)、`_extract_refs_dict_from_tool_result`(:127-152，逻辑改造成 `_refs_dict_from_search_results`) |
| `backend/app/ai/agents/__init__.py` | 移除对 `build_interp_agent`、`InterpKnowledgeMiddleware` 的导出（:9-12） |
| `backend/app/ai/agents/tools.py` | `INTERP_TOOLS`（:190）若无其它引用可删/保留；`AgentContext` 仍被 chat 用，保留 |
| `backend/tests/test_toolcall_guard.py` | 该守卫退役后：删除或改为"确定性检索不产生 tool_call"的测试（见 §6） |
| `backend/app/config.py:104` | `AGENT_MAX_ITERATIONS` 在此步不再使用；确认无其它引用后可保留（chat 可能仍用）或标注 |

删除前务必 `grep -rn` 确认无其它调用点（已知 `agents/__init__.py` 导出、`interp_graph.py` 内部）。

---

## 5. 必须注意的点（踩坑清单）

1. **契约不变**：下游 `_generate_report`、`_merge_citations`、`persist`（`summary_refs`）
   依赖 `knowledge_results`/`references` 的**形状与语义**；改造后必须逐字段对齐，否则引用/
   去重/落库会坏。建议先读 `interp_graph.py:329-339 _merge_citations` 与 `persist`(:650-701)。
2. **指标名要与 `indicator_judgment` 一致**：executor 的归属用 `abnormal_indicators` 的
   `item_name`（与 `filter_abnormal` 输出的 `j["item_name"]` 一致），不要改成 standard 名，
   否则引用与判定对不上。
3. **空结果合法，不是错误**：`FPSA/TPSA` 原名检索 0 条（实测），`游离前列腺特异性抗原`
   有 3 条。executor 必须"空则试 fallback，仍空就跳过该指标"，**绝不抛异常**。
4. **去重**：`filter_abnormal` 可能产出重复指标名（`term_normalizer.py:512-519` 有说明），
   planner 先去重，避免重复检索/重复引用。
5. **确定性**：本步不得调用 `get_chat_model`/MedGo。这是根治点——若将来确实需要模型选词，
   只允许加**单次结构化输出** planner（`with_structured_output`，无工具、无迭代、有
   `max_tokens`/超时上限），**严禁**再引入"模型看结果再决定"的循环。
6. **相关加固（建议一并做，但可分开）**：
   - `app/ai/llm.py:get_chat_model` 的 OpenAI client **默认 `max_retries=2`**，会把一次 600s
     超时放大成 30min；给模型显式设 `max_retries=0`（或很小）。
   - 解读任务超时 vs RabbitMQ `consumer_timeout`（默认 30min）撞车会**丢消息**（本次即如此）。
     建议加"解读看门狗"：把 `status in ('processing','pending')` 且超 N 分钟无进展的行重新入队
     （`run_interpretation_agent` 已有"已完成则跳过"的幂等判断，安全）。
7. **重启生效**：改的是 worker 链路，验证前必须重启 3 个 interpretation worker（`ps -eo pid,lstart` 核对）。
8. **不要改**：chat 的 planner/executor（`chat_planner.py`）、`_generate_report`、judge 链路。

---

## 6. 验证要求（遵守仓库《指标提取/总检异常链路与验证纪律》）

本改动属**生成逻辑改动**，验收 = 测试 + 端到端（端到端仅在用户发令后执行）。

- **单元测试（新增）**，建议 `backend/tests/test_interp_knowledge_search.py`：
  1. planner 对 `["FPSA/TPSA","尿酸","总胆固醇","血糖","尿酸"]`（含重复）→ 去重为 4 条、
     每条 query=指标名、`FPSA/TPSA` 带 fallback（含拆分/归一候选）。
  2. executor 对空结果走 fallback；构造一个 fallback 命中的假 `ai_rag.search`，断言合并了结果。
  3. **无 LLM 调用**：monkeypatch `app.ai.llm.get_chat_model`（或 `build_interp_agent`）
     为抛异常，跑 `agent_search_knowledge`/整个 graph 的该步，断言**不抛、能产出 knowledge_results**。
  4. refs 形状断言：文档 key=int entry_id；KG key=`kg:<title>`，两类的字段集与现状一致。
- **回归样本**：用 report 31 的异常集（`FPSA/TPSA`、`尿酸`、`总胆固醇`、`血糖`）作为用例，
  断言该步在秒级完成、不触达 MedGo。
- `tests/test_toolcall_guard.py` 相应删除/改写（守卫退役）。
- 全量后端测试跑一遍（`cd backend && .venv/bin/python -m pytest -q`，注意已知的既存失败）。
- 端到端（用户发令后）：重跑一份含"检索为空的指标"的报告，确认解读完成、引用正常、
  不再出现 30min 卡死。
- **离线/秒级优先**：先纯函数单测（planner/executor）+ 假 search 的重放，不要每轮真调 MedGo。

---

## 7. 非目标

- 不改总检建议/结论提取（`report/service.py`）。
- 不改 `_generate_report` 与 judge。
- 不改 chat 的 planner/executor。
- 不要求删除 `INTERP_TOOLS`/`AgentContext`（若无引用自然清理，但不强求）。
- 不排查/修复其它无关的 MedGo 退化点。

---

## 8. 开放问题（留给实现者决策）

1. fallback 策略的"力度"：`term_normalizer` 归一到什么程度、斜杠拆分是否总是安全（可能引入
   错配）。建议保守：只做「归一化名」+「斜杠拆分」，且仅在首选 0 结果时使用；并把每次
   fallback 命中写 `app.interp` 日志以便回溯。
2. 是否给本步加 `top_k` 统一值（现状工具用默认）。建议保持默认，避免改变召回分布。
3. 看门狗（§5.6）是否纳入本次提交：建议纳入，但它会写 DB/重投消息，需单独测试。

---

## 附：本次排查用的关键证据位置

- `interp_graph.py:49-58`（`SEARCH_SYSTEM_PROMPT`）、`:306-320`（`build_interp_agent`）、
  `:615-632`（`agent_search_knowledge`）、`:627`（`recursion_limit`）
- `chat_planner.py:74-97`（planner）、`:218+`（executor）
- `app/config.py:104`（`AGENT_MAX_ITERATIONS`）
- `app/ai/llm.py:12-16`（`_guarded`）、`get_chat_model`（未设 `max_retries`）
- `interpretation/worker.py:174-205`（失败重投/DLQ）、`app/core/rabbitmq.py:143-160`（consume ack）
- 日志：`/data/logs/app.log`（`report=31` 相关行）、`/data/logs/vllm-medgo.stdout.log`（600s 重发）
- 实测：`ai_rag.search('H004','FPSA/TPSA') -> 0`；`'游离前列腺特异性抗原' -> 3`
