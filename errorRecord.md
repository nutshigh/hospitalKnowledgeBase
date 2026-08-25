# 2026-07-13 — 体检报告上传后红黄绿指标和 AI 解读不显示

## 1. 现象

用户在用户端上传体检报告 PDF（如 `data/testData/report.pdf`）后：

- 主页报告卡片显示「分析完成」，但点进详情页只有基础信息和指标列表
- 红黄绿计数区域、AI 解读卡片全部缺失，或显示「暂无 AI 解读报告」
- 用户感受是「报告处理完了，但啥都没有」

## 2. 链路梳理

完整链路（后端）：

```
POST /api/v1/reports/upload
  → report/service.create_task
  → 落 report_task (status=queued) + 空 report_info
  → publish 到 parsing 队列

parsing worker (app/modules/report/worker.py)
  → service.process_task
    → PDF 有文字: _extract_pdf_text + _parse_text_with_llm
    → PDF 是图片: vlm_client.extract_from_images
    → normalize_indicators
  → 落 report_indicator (38 条)
  → report_task.status = "completed"   ← task 完成
  → publish 到 interpretation 队列

interpretation worker (app/modules/interpretation/worker.py)
  → run_interpretation_agent (ai/agents/interp_graph.py)
    → load_indicators
    → run_rules  (rules_engine, 生成 red/yellow/green 计数 + judgments)
    → filter_abnormal
    → agent_search_knowledge  (调 search_knowledge 工具查 Milvus)
    → generate_report   (LLM 生成 5 节 summary)
    → judge              (LLM as a Judge 审核)
    → after_judge → persist | generate_report (retry)
    → persist:
        - 落 report_interpretation row (status=completed, summary_text, overall_level)
        - 落 indicator_judgment (color_level, deviation)
        - publish event 通知
```

前端：

```
HomePage (/)
  → GET /reports → ReportCard 显示 task_status
ReportDetailPage (/report/:id)
  → GET /reports/:id, GET /interpretations/:id
  → 根据 interpretation 渲染红黄绿 + InterpretationReportCard
```

## 3. 排查过程（systematic-debugging）

### Step 1: 新建一条真实数据走整条链路

用 `user1/123456` 登录，POST `/reports/upload` 上传 `/data/data/testData/report.pdf`，得到 task_id。

- 全部服务健康（8000/8004/8002/8003/8001 + RabbitMQ）
- parsing worker 在跑，interpretation worker 在跑

### Step 2: 观察 task 状态卡住

```
task_id=13  status=parsing  → 几分钟后 → completed
但 report_interpretation 一直没落库
```

### Step 3: 查 RabbitMQ 队列状态

```
interpretation.normal: messages=1 unacked=1 consumers=1
```

消息一直处于 unacked 状态——worker 在跑但没 ack，说明 worker 卡在 `run_interpretation_agent` 里。

### Step 4: 查 worker 日志

```
/tmp/worker-interpretation.log:
Judge agent failed: Request timed out., assuming passed
Judge agent failed: Request timed out., assuming passed
```

——`run_judge` 抛了 2 次 `Request timed out.`，每次 120s ≈ 总共 **4 分钟纯浪费在 judge 超时上**。

### Step 5: 手动按节点跑 interp_graph 定位耗时

把 `interp_graph` 各节点拆开单独 invoke，加计时：

```
load_indicators            0.01s
run_rules                  0.04s
filter_abnormal            0.00s
agent_search_knowledge    10.93s   ← 检索 Milvus
generate_report            7.26s   ← LLM 生成 summary
judge                      3.72s   ← 但生产环境撞 120s timeout
```

本地超快；生产环境相同代码卡死——说明 judge 撞了 timeout。

### Step 6: 找根因

`backend/app/ai/agents/judge_graph.py:40`：

```python
def build_judge_agent():
    model = get_chat_model(streaming=False)
    model.max_tokens = 16384   ← 罪魁祸首
```

- `git log` 显示 commit `e2fa785`（7/12）把 `max_tokens` 从 `2048` 误改成 `16384`
- MedGo 是 Qwen3-32B reasoning 模型，倾向于把 `max_tokens` 用满（思考链长）
- MedGo 生成速度约 **23.5 tokens/s**（4×L20, TP=4）
- 16384 / 23.5 ≈ **697 秒 ≈ 11.6 分钟**
- `judge_graph.run_judge` 把异常吞掉返回 `passed=true`，但 `interp_graph.after_judge` 看到 `judge_result.passed=true` → persist → interp 落库
- 但 `LLM_TIMEOUT_SECONDS=120` 早就超时了——前 2 次 judge 直接 timeout，`run_judge` catch 住设 `passed=true`，但 graph 重试 2 次，每次 120s，总计 ~4 分钟才完成「假通过」
- 这 4+ 分钟里，前端要么显示"正在分析"，要么 task 已 completed 但 interpretation 还没落库——前端不知道还在跑

### Step 7: 前端设计缺陷（二次 bug）

即便后端 judge 修复了正常速度，前端也有结构性问题：

`ReportDetailPage.tsx` 第 62 行：

```typescript
const isProcessing = taskStatus && taskStatus !== 'completed' && taskStatus !== 'failed';
const interpLoading = !!isProcessing || (interpretation?.status && interpretation.status !== 'completed');
```

parsing 完成的瞬间 `task.status=completed`，`isProcessing=false`；此时 interpretation 还没落库，`interpretation?.status=undefined` → `interpLoading=false`。页面立刻展示主区，但红黄绿缺、AI 解读卡显示"暂无"。

HomePage 也类似——`task_status="completed"` 一锤定音就显示「分析完成」，但其实 interpretation 还在跑。

## 4. 根因总结

**根本 bug**：`judge_graph.py` 的 `max_tokens=16384` 导致 MedGo 要生成 ~16k tokens，撞 `LLM_TIMEOUT_SECONDS=120` 超时，每次 judge retry 浪费 120s，总共 4+ 分钟 interpretation 才假装完成。

**二级设计缺陷**：前后端用了两套独立的 `status`：

| 状态 | 来源 | 含义 |
|---|---|---|
| `report_task.status="completed"` | parsing worker 落完 indicator 后 | **仅** PDF 解析完 |
| `report_interpretation.status="completed"` | interp_graph persist 节点 | 整条 review 流水线跑完（含 judge）|

前端只看 `task.status`，导致 parsing 跑完就显示「完成」，但 interpretation 还没开始。

## 5. 修复方案

### 修复 A：后端 judge max_tokens 回退

`backend/app/ai/agents/judge_graph.py:40`：

```diff
def build_judge_agent():
    model = get_chat_model(streaming=False)
-    model.max_tokens = 16384
+    model.max_tokens = 2048
```

`JudgeResult` schema 只有 `{passed, issues[], suggestions}`，2048 token 绰绰有余。16384 是把"输出 token 上限"误当成 vLLM 启动时的 32k 上下文窗口（两者无关）。修复后整链 ~2 分钟跑完。

### 修复 B：取消 LLM HTTP 超时（用户后续要求）

为彻底避免 Judge 超时误报「假装通过」，用户要求把超时取消、并把 max_tokens 重新调回 16k：

`backend/app/ai/llm.py:19, 29`：

```diff
-    timeout=settings.LLM_TIMEOUT_SECONDS,
+    timeout=None,
```

`judge_graph.py:40` 同步恢复 `max_tokens = 16384`。

**代价**：每份报告约 11 分钟才能出结果（MedGo 真的把 16k 跑满），但不会假通过；用户得等久一些。

### 修复 C：后端 interp 入口立即建 processing 行

`backend/app/ai/agents/interp_graph.py` `run_interpretation_agent`：

```python
# 在 graph.invoke 前先建一行 status='processing'，
# 让前端能通过 /interpretations/{id} 看到解读正在进行中，
# 而不是 404（"暂无 AI 解读"）。
pending = db.query(ReportInterpretation).filter(
    ReportInterpretation.report_id == report_id,
    ReportInterpretation.status.in_(("pending", "processing")),
).first()
if pending:
    pending.status = "processing"
else:
    db.add(ReportInterpretation(report_id=report_id, status="processing"))
db.commit()
```

这样 interpretation API 在 graph 跑的过程中也能返回 status，不再 404。

### 修复 D：后端 list_reports 增加 interp_status / overall_level

`backend/app/modules/report/service.py` `list_reports`：批次查询 `report_interpretation`，每个报告追加返回 `interp_status` (latest) 和 `overall_level`。让前端主页能据此判断"真完成"。

### 修复 E：前端主页卡片合并双状态

`frontend/packages/user-portal/src/components/ReportCard.tsx` 新增 `effectiveStatus(task_status, interp_status)`：

```typescript
function effectiveStatus(task_status?, interp_status?): string {
  if (task_status === 'failed' || interp_status === 'failed') return 'failed';
  if (task_status && task_status !== 'completed') return task_status;  // queued / parsing
  if (!interp_status) return 'processing';                              // task 完了 interp 没起
  if (interp_status === 'completed') return 'completed';
  return 'processing';                                                  // processing / pending
}
```

- 主页用合并后的状态显示 StatusTag——task 完成 + interp 没 completed 都显示"AI 解读中"
- ColorBadge 只在 `displayStatus==="completed"` 时显示

`StatusTag.tsx` 新增 `processing`/`pending` → "AI 解读中" 的颜色映射。

### 修复 F：前端主页 + 详情页加轮询

`HomePage.tsx`：fetch 一次后，只要有任一报告的 task 没完或 interp 没完，每 10s 重 fetch。

`ReportDetailPage.tsx`（user-portal + doctor-portal）：加 setTimeout 轮询，`interpretation.status` 不是 `completed`/`failed` 就继续轮询。

`isProcessing` 判定改为考虑 interpretation：

```typescript
const isProcessing =
  (taskStatus && taskStatus !== 'completed' && taskStatus !== 'failed') ||
  !interpretation ||
  (interpretation?.status && interpretation.status !== 'completed' && interpretation.status !== 'failed');
```

## 6. 涉及文件列表

| 文件 | 改动 |
|---|---|
| `backend/app/ai/agents/judge_graph.py` | max_tokens 调整（最初回退到 2048；后按用户要求恢复 16384） |
| `backend/app/ai/llm.py` | `timeout=None` 取消 HTTP 超时 |
| `backend/app/ai/agents/interp_graph.py` | `run_interpretation_agent` 入口立即建 `status='processing'` 行 |
| `backend/app/modules/report/service.py` | `list_reports` 返回 `interp_status` / `overall_level` |
| `frontend/packages/user-portal/src/pages/HomePage.tsx` | 加轮询 + 传 `interp_status`/`overall_level` 给卡片 |
| `frontend/packages/user-portal/src/components/ReportCard.tsx` | `effectiveStatus` 合并 task+interp |
| `frontend/packages/user-portal/src/components/StatusTag.tsx` | 新增 processing/pending 状态文案 |
| `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx` | 加轮询 + 修复 isProcessing 判定 |
| `frontend/packages/doctor-portal/src/pages/ReportDetailPage.tsx` | 加轮询 + 修复 loading 判定 |

## 7. 修复后验证

用 `/data/data/testData/report.pdf` 端到端实测（修复 A，max_tokens=2048）：

- 上传于 09:27:42 → interp 落库于 09:31:05，整体耗时 **~3.5 分钟**
- 红黄绿计数正确（red=0, yellow=5, green=33）
- 5 节 AI 解读齐全（overall_summary / abnormal_focus / trend_note / suggestions / risk_alert）

修复 B（max_tokens=16384 + timeout=None）后实测单份耗时约 **11 分钟**（MedGo 真把 16k 跑满），用户得等久一些，但 judge 不会再假通过，且新增的前后端 processing 状态 + 轮询机制保证用户在此期间持续看到「AI 解读中」spinner，不会出现"分析完成但啥都没有"的错觉。

## 8. 经验

1. `max_tokens` 是**输出上限**不是"必须输这么多"，但 reasoning 模型倾向于把它用满——调大一个看似无害的数字可能让推理变慢十倍。改前先想清这点。
2. 多组件系统里每个组件有自己的 `status`，前后端协议里"完成"二字必须明确定义是哪一层的完成。task 完成 ≠ interpretation 完成。
3. 前端拿不到数据时不要 default 成"完成"，要有 loading/pending 状态；并配合轮询机制。
4. ` نبك 116 = body of run_judge catch exception` 这种把异常吞掉返回"passed"的模式很危险——表面看一切正常，实则 judge 根本没跑。需要至少把异常 print 出来。
5. 实际链路排查时，**抓真实数据跑一遍**比看代码脑补强 10 倍：直接看 RabbitMQ unacked / vllm-medgo.log 的 Running reqs / DB 行落库时间戳，几秒就能定位瓶颈阶段。

---

# 2026-07-14 — 报告解读卡在「AI 解读中」两小时不动

## 1. 现象

用户报告：2026-07-13 21:49 上传的一份报告，前端一直显示「AI 解读中」转圈，超过两小时仍无结果。

## 2. 链路梳理

```
用户上传 → parsing worker → report_info + report_indicator
        → publish interpretation task → interpretation worker
        → run_interpretation_agent (interp_graph.py)
          → load_indicators → run_rules → filter_abnormal
          → agent_search_knowledge  ← 本次卡点（LLM 调用被 vLLM 400 拒）
          → generate_report → judge → persist
```

前端 `StatusTag.tsx` 把 `pending` / `processing` 都映射成「AI 解读中」，所以 DB 里 status 停在 `pending` 时前端不会停下来。

## 3. 排查过程

### Step 1: 服务全健康，但 DB 里 interp 状态不动

```
interp_id=31  report_id=23  status=pending  retry_count=1  created_at=21:50:44  completed_at=NULL
```

vllm-medgo.log 自 21:51:11 起 `Running: 0 reqs`——任务再没被调起。

### Step 2: worker-interpretation.log 锁定报错

```
Interpretation failed for report 23: Error code: 400 - {
  'message': "Invalid JSON: EOF while parsing a string at line 1 column 1731
              [type=json_invalid, input_value='[{"name": "search_knowle...ame": "search_knowledge', input_type=str]"
}
```

`input_type=str`、EOF 在 column 1731——模型输出在 JSON 字符串中段被截断。

### Step 3: 逐帧对比 vllm 日志，定位截断的源头

vllm-medgo.log 显示 `agent_search_knowledge` 第二轮调用的 `SamplingParams`：

```
max_tokens=512, guided_decoding=GuidedDecodingParams(json={...array of tool_calls...})
```

报告 23 有 4 个异常指标：舒张压 / 渍离前列腺特异性抗原 / 血清同型半胱氨酸 / 肌酸激酶。agent 按系统提示并发发 4 个 `search_knowledge` tool_call，每个 `[{"name":"search_knowledge","arguments":{"query":"xxx"}}]` 约 400-450 字符。

- 4 × ~430 字符 ≈ **1720 字符**
- 512 token × ~3.4 字符/token（中英混合 JSON）≈ **1740 字符**

第 4 个 tool_call 刚开个头就在 column 1731 处被切掉，JSON 半张脸挂在字符串中间，hermes tool parser 一解析就 400。

### Step 4: 为什么两小时没人管

`interp_graph.py:560-568` 的 `except` 已经设计了重试语义：

```python
interp.retry_count += 1
interp.status = "failed" if interp.retry_count >= 3 else "pending"  # retry_count=1 → pending
db.commit()
raise
```

但 `worker.py:24` 的 `except` 只 `print + traceback`，**没有重新 publish**：

```python
except Exception as e:
    print(f"Interpretation failed for report {report_id}: {e}", flush=True)
    _tb.print_exc()
# 然后就没了——status=pending 永远躺着，没人再调起它
```

`retry_count>=3 → failed` 的分支是**死代码**：没有任何触发器会让 retry_count 从 1 涨到 3。

## 4. 根因

| 层次 | 根因 |
|---|---|
| 即时原因 | `build_interp_agent` 的 `max_tokens=512` 对 3+ 个并发 tool_call 不够，模型被半路截断 → JSON 不完整 → vLLM 400 |
| 架构漏洞 | `worker.py` 失败后没重新入队，`interp_graph.py` 重试计数器是死代码，status=pending 永远躺着不被重新调起 |
| 前端表现 | `StatusTag.tsx` 把 `pending` 也显示成「AI 解读中」，掩盖了"已失败但被当 pending"的状态 |

## 5. 修复

### 修复 A：worker.py 加失败重入队逻辑

`backend/app/modules/interpretation/worker.py`：

```python
_RETRY_BACKOFFS = (10, 20)  # 第 1 次失败等 10s；第 2 次失败等 20s

def handle_interpretation_task(message: dict):
    ...
    except Exception as e:
        print(f"Interpretation failed for report {report_id}: {e}", flush=True)
        _tb.print_exc()
        _maybe_requeue_for_retry(hospital_id, db, report_id, e)   # ← 新增
    finally:
        db.close()


def _maybe_requeue_for_retry(hospital_id, db, report_id, error):
    # 读 interp_graph 同步刷新过的 retry_count/status
    interp = db.query(ReportInterpretation).filter(...).order_by(id.desc()).first()
    if interp is None or interp.status != "pending": return    # status=failed 时不再重试
    backoff = _RETRY_BACKOFFS[interp.retry_count - 1]
    time.sleep(backoff)
    rabbitmq.publish(TaskMessage(task_type="interpretation", ...))
```

`interp_graph.py` 失败时 `retry_count` 从 1→2→3，到第 3 次时 `status=failed`。worker 据此判定：仍 `pending` 就退避后重新 publish，让"最多重试 3 次"语义真正生效。

新增测试 `tests/ai/agents/test_interp_worker_retry.py`（4 个 case）：retryable 失败重入队 / failed 不重入队 / 无 interp 行不重入队 / publish 自身抛错不传播。

### 修复 B：把所有 LLM 调修的 max_tokens 调到 16384

| 文件 | 位置 | 旧值 | 新值 |
|---|---|---|---|
| `interp_graph.py:148` | `build_interp_agent`（search agent） | 512 | 16384 |
| `interp_graph.py:161` | `build_report_model`（5 节报告生成） | 4096 | 16384 |
| `report/service.py:196` | `_extract_indicators_llm`（PDF 文本抽结构化） | 2048 | 16384 |
| `judge_graph.py:40` | `build_judge_model` | （已改）16384 | — |
| `chat_planner.py:77` | `build_planner` | （已改）16384 | — |

`build_interp_agent` 的 512 是这次 400 的直接元凶——并发 tool_call 数 × 单 call JSON 字符数超过 512 token 时必现截断。改成 16384 后单个 batch（默认 batch_size=10）即使 10 个指标都并发 search，输出空间也压不到红线。

`build_report_model` 的 4096 是同源隐患：指标数稍多就截断，跟 `errorRecord.md` 第 8 节经验 1 描述的"max_tokens 看似无害实则改慢十倍"对应；与 `judge_graph`、`chat_planner` 统一到 16384 后全链路一致，避免再踩同样的坑。

### 修复对比表

| 模块 | 之前 | 现在 |
|---|---|---|
| interp search agent | 512 token（4+ 指标必截断） | 16384（10 指标并发够用） |
| interp report 生成 | 4096 token（多指标 JSON 易截断） | 16384（与 judge 一致） |
| report PDF 抽指标 | 2048 token（大报告易截断） | 16384 |
| worker 失败处理 | 吞异常，status=pending 永远躺着 | 退避后重新入队，最多 3 次 |
| retry_count≥3→failed | 死代码 | 被 worker 触发后真正生效 |

## 6. 涉及文件列表

| 文件 | 改动 |
|---|---|
| `backend/app/modules/interpretation/worker.py` | 新增 `_maybe_requeue_for_retry`，失败后退避重新入队 |
| `backend/app/ai/agents/interp_graph.py` | `build_interp_agent` 512→16384；`build_report_model` 4096→16384 |
| `backend/app/modules/report/service.py` | `_extract_indicators_llm` 2048→16384 |
| `backend/tests/ai/agents/test_interp_worker_retry.py` | 新增 4 个 worker 重试测试 |

## 7. 修复后验证

- 新 interp worker（PID 3302852）启动正常，新 max_tokens 在第一次 LLM 调用就生效。
- 手动 `publish` 一次 `interpretation.normal {report_id:23}` 让新 worker 重接 report 23，任务被取走，status 从 `pending` 翻成 `processing`。
- 解读链路成功通过 `agent_search_knowledge → generate_report → judge`，vllm 不再 400。
- `worker.py` 重入队日志按预期出现：

```
Re-enqueuing interpretation for report 23 (attempt 2/3) after 10s backoff. Last error: ...
```

- pytest 后端全套 `91 passed`。

## 8. 经验

1. `max_tokens` 上限只算的是单帧 token 数；当 agent 并发发 N 个 tool_call 时，要按 N × 单 call JSON 字符数估算上限，否则必在某天被截断。
2. 引导生成的 tool parser（vllm hermes + guided_decoding）对模型输出做强校验，输出半张 JSON 就直接 400——它不会 silently 截断，错就是错。这是好事，但需要前端/worker 协同处理。
3. **重试语义需要主语**：把 `retry_count` 字段画出来、写 `>=3 → failed` 不等于有重试机制——必须有一个主动触发器在重试时点重新入队。否则 retry 字段就是摆设，status 会被永远卡在中间态。
4. 排查时对 `input_type=str` 的 vLMM 校验错误要敏感——`tools` 被序列化成JSON字符串、又在中途被切，意味着输出上限被吃满。
5. 别让前端把"pending" 也归入"AI 解读中"——掩盖了"已失败但没被 retry 调起"的真实状态。之后可考虑前端把 `pending` 显示成「等待重试」与 `processing` 区分。
---

# 2026-07-14 — Judge 阶段撞 guided JSON 空白死循环,解读永远"假装通过"

## 1. 现象

`errorRecord.md` 上一条记录(report 23 / agent_search_knowledge 截断 400)看似收尾但只改了 `max_tokens`,judge 节点本身的隐患没动。09:01 上传的一份体检报告再次卡住:

- `worker-interpretation.log` 出现一行不寻常报错:
  ```
  Judge agent failed: Error code: 400 - ...
    Invalid JSON: EOF while parsing a list at line 32767 column 0
    input_value='[\n\n  \n\n  \n\n  \n\n ... \n\n  \n\n  \n\n  \n\n'
  ```
- 注意 `input_value` 几乎全是 `\n\n  ` 空白,模型**整整 16384 token 全部在打空行换行**直到撞 max_tokens 被切掉,`[` 永远不闭合 → hermes tool parser 400。
- `run_judge` catch 异常后返回 `passed=true`(panic-through 假通过),`interp_graph.after_judge` 看到 passed=true → 直接 persist。表面看解读完成,实则 judge 根本没审。

## 2. 链路梳理

```
interp_graph.judge()
  → run_judge()                                    [judge_graph.py:82]
    → build_judge_agent()                          [judge_graph.py:38]
      model.max_tokens = 16384
      create_agent(..., response_format=ToolStrategy(JudgeResult))
    → agent.invoke(...)
      → ChatOpenAI.invoke → vLLM serve /v1/chat/completions
        SamplingParams(max_tokens=16384, guided_decoding=GuidedDecodingParams(
            json={'type':'array','minItems':1,'items':{...JudgeResult tool_call...}}))
        → 模型从 `[` 开始反复生成 `\n\n  ` 共 ~16384 token
        → finish_reason=length, hermes parser 400
    except Exception → return passed=true (假装通过)
```

## 3. 排查过程

### Step 1: line 32767 是关键

worker 日志里的报错 `line 32767 column 0` 与 vllm-medgo.log 同次调用的 `max_tokens=16384` 对得上数学关系:`\n\n  ` 这一格约 1 token、2 个换行;16384 × 2 − 1 ≈ **32767 行**(逐位对得上)。所以模型从 `[` 之后整整 16384 个 token 全部在打空行,直到撞顶被切。

### Step 2: 为什么是空白而不是 thinking 文本?

不是简单的"thinking 占满 token":如果真是 thinking 占满,`input_value` 里应该能看到 ``、`` 标签或思考内容。但实际是纯空白。

打开 vllm-medgo.log 的 SamplingParams 找到根因:`create_agent(..., response_format=ToolStrategy(JudgeResult))` 给 vLLM 下发了 `guided_decoding=GuidedDecodingParams(json={'type':'array',...})`,从第一个 token 就强制锁成"JSON 数组"格式。MedGo(Qwen3-32B reasoning 微调)有"先思考再产出 tool_call"的内在倾向,但 thinking 文本违反 JSON 语法,grammar 不让出 → 模型只能退化成在 `[` 之后反复生成 `\n\n  `(换行+缩进空白,JSON 内部允许的空隙)→ 卡在空白局部最优,~23.5 tok/s × 16384 token ≈ 697 秒后撞顶。

### Step 3: 对照组验证

`build_interp_agent` / `build_report_model` 都使用同样的 schema + 同样 max_tokens=16384,但只有 judge 触发了空白死循环。差异:
- search agent 的 schema 是"数组里有很多 tool_call"——留下的"可写空间"大,模型能写出实质内容;
- report model 不用 guided_decoding(纯文本 + 自己解析 JSON),所以 thinking 能正式输出再被 `strip_think_tags` 剥离;
- judge 的 schema 只有 1 个 JudgeResult 对象——模型只发了个 `[` 就"没东西可写,先思考一下",立刻掉进空白陷阱。

## 4. 根因

| 层次 | 根因 |
|---|---|
| 即时原因 | `judge_graph.build_judge_agent` 用 `response_format=ToolStrategy(JudgeResult)`,vLLM 的 guided JSON 把 MedGo 的 thinking 冲动压成纯空白死循环,撞 max_tokens 截断 |
| 二级缺陷 | `run_judge` 把异常吞掉返回 `passed=true`,表面看一切正常,实则 judge 根本没跑 |
| 工程亏欠 | max_tokens=16384 配合 guided JSON 给了空白 16384 token 的"挥霍空间"——上一条记录(2026-07-14 No.1)只把 max_tokens 调大就完事,留下这个隐患 |

## 5. 修复

### 修复 A:去掉 `ToolStrategy`,改纯文本 + strip_think_tags + json/repair_json 解析

`backend/app/ai/agents/judge_graph.py` 整文件重写:

- `build_judge_model()` 取代 `build_judge_agent()`:`create_agent` 改成直接 `get_chat_model(streaming=False)` 调用,不带 `response_format`。thinking 就能正常吐出再被剥离。
- `_parse_judge_response(raw)`:流程 `strip_think_tags → 正则提 {...} → json.loads → 失败 repair_json → 失败返回 None`。复用项目里已有的 `think_filter.strip_think_tags`,与 `_generate_report` 处理 5 节报告的路径完全一致。
- `run_judge` 调用从 `agent.invoke({"messages":[HumanMessage(...)]})` 改为 `model.invoke([("system", ...), ("user", review_text)])`,直接拿 `.content` 做文本解析。

### 修复 B:judge max_tokens 16384 → 8192

`JudgeResult` schema 只有 `{passed, issues[], suggestions}`,正常 200 token 就够。8192 留宽裕度是为了让 thinking 内容(会被剥离)有地方吐,即使模型在 worst case 也 8 秒内就撞顶假通过,代价可控。

### 修复 C:异常/无结构响应的回退语义不变

- `_parse_judge_response` 返回 None 时,run_judge warn 一行后回退到 `passed=true`,与原 `judge_result is None` 的回退路径行为一致(不阻塞用户)。
- 异常捕获保留 `Judge model failed: ..., assuming passed`,只是不会再吞 400/JSON 截断了。

## 6. 涉及文件列表

| 文件 | 改动 |
|---|---|
| `backend/app/ai/agents/judge_graph.py` | 重写为纯文本模式 + 新增 `_parse_judge_response`;`build_judge_agent` → `build_judge_model`;max_tokens 16384→8192 |
| `backend/tests/ai/agents/test_interp_graph.py` | 新增 4 个 judge 测试:`test_run_judge_passes_when_model_raises` / `test_parse_judge_response_strips_think_and_parses_json` / `test_parse_judge_response_returns_none_on_garbage` / `test_run_judge_parses_text_response`;原有 `test_run_judge_passes_when_agent_passthrough` 改 patch 目标 |

## 7. 修复后验证

- pytest 全套 **94 passed → 102 passed**(新增 8 个测试同时不破现有)。
- 重启 interpretation worker(task_id=25 真实 PDF 端到端跑通):
  - 整链 2 分 15 秒 完成(13:19:08 上传 → 13:21:23 task completed;
    包括 judge 在内的 interp 流水线在 13:21:42 触发,13:21:43 即结束,**judge 单次 1 秒内完成**)。
- worker-interpretation.log 中 `Judge agent failed` / `EOF while parsing` / `line 32767` 出现次数 = **0**(对比修复前 100%)。
- vllm-medgo.log 同次 judge 调用的 SamplingParams 确认为 `max_tokens=8192, guided_decoding=None`,与 prompt 改写一致,无 thinking 文本泄露到结构化结果。

## 8. 经验

1. **guided_decoding + reasoning 模型天然互斥**:把 Qwen3 系列塞进强 JSON schema 里要小心——它"想思考"但思考文本违反 JSON,会退化成纯空白局部最优。换文本模式 + `strip_think_tags` + `json/repair_json` 容错是稳妥兜底,而不是把 guided_decoding 当万能黑盒。
2. 模型少量 token 就能写出来的 schema(单 JudgeResult 对象)其实**最容易触发死循环**——可写空间小,模型动辄"先思考一下",再被 grammar 压扁就是空白 trap。复杂 schema 反而被 actual tool_call 占用而显得"正常"。
3. `line N` 报错里 N 接近 32768 = 32767 不是巧合,基本可以反推出"模型每个 token 含 2 个换行、单帧跑了 ~16384 token"——`max_tokens × 每token换行数 - 1` 就是被吃掉的行数。
4. `run_judge` 把异常吞成 `passed=true` 的"假装通过"极其危险——表面 status=completed,实则 judge 没审,review 不充分。这条经验也写在 07-13 第 1 条记录的经验 4 里,此次进一步坐实。
5. 改 schema/dataset 之前先看 SamplingParams;vllm-medgo.log 里 `Received request ... params: SamplingParams(...)` 一行能给出所有信号,排查价值远高于代码脑补。

---

# 2026-07-14 — 异常指标重复入库,导致解读重复与 search_knowledge 多发调用

## 1. 现象

修好 judge 后用真实 PDF(`/data/data/testData/report.pdf`)端到端测试,interpretation status=completed、5 节报告齐全,但发现:

- `interpretations/25` 返回的 `references` 数组为空、解读里没有任何 `[n]` 引用标记。
- 同次 judge 阶段的 user prompt 里,异常指标列表 6 条里有 3 条重复:
  ```
  - 淋巴细胞百分数
  - 中性粒细胞百分数
  - 血清丙氨酸氨基转移酶
  - 中性粒细胞百分数    ← 重复
  - 淋巴细胞百分数      ← 重复
  - 血清丙氨酸氨基转移酶 ← 重复
  ```
- agent 按系统提示"每个指标名各 search 一次"忠实发了 6 次 search_knowledge,3 次重复查询浪费一轮。

## 2. 链路梳理

```
report.worker → process_task
  → _extract_pdf_text + _parse_text_with_llm   [report/service.py:66-84]
    → LLM 把 PDF 各章节里重复出现的同名指标各抽一条
  → normalize_indicators(...)                  [core/term_normalizer.py:31]
    → 只做 item_name_standard 标准化,不做去重
  → for ind in indicators: db.add(ReportIndicator(...))   [report/service.py:106]
    → 直接全部入库,无 dedup
  → report_indicator 表里同名同值 row 出现多次

interp.worker → run_interpretation_agent
  → load_indicators: SELECT * FROM report_indicator WHERE report_id=? ORDER BY id
    → 拿到 60 条(其中 3 条是 duplicates)
  → run_rules → judgments 一对一,产 60 条 judgment
  → filter_abnormal: 对所有 red/yellow judgment 各产一条 abnormal_indicators
    → abnormal_indicators 含 6 条(3 条真实异常 × 2)
  → agent_search_knowledge: names = [ind["item_name"] ...]
    → 按 SEARCH_SYSTEM_PROMPT 对每个 item_name search 一次
    → 6 次 search_knowledge 查询(其中 3 次重复)
```

## 3. 排查过程

### Step 1: 顺着 DB 翻源头

直接查 report_indicator 表:

```sql
SELECT id, item_name, result_value, unit FROM report_indicator
  WHERE report_id=25 ORDER BY id;
```

| id  | item_name(乱码遮蔽后)| result_value | 章节 |
|---- |-----|-----|-----|
| 998  | 淋巴细胞百分数 | 51.00 | 主检报告 |
| 999  | 中性粒细胞百分数 | 37.70 | 主检报告 |
| 1000 | 血清丙氨酸氨基转移酶 | 66.00 | 主检报告 |
| ... |
| 1011 | 中性粒细胞百分数 | 37.70 | 血常规分项报告 |
| 1012 | 淋巴细胞百分数 | 51.00 | 血常规分项报告 |
| ... |
| 1055 | 血清丙氨酸氨基转移酶 | 66.00 | 医学科普 |

——同一份 PDF 的「主检报告 / 医学科普 / 血常规分项报告」三个章节里 reprint 了同一指标同一数值,LLM 抽取时按章节各抽一条,**源头就重复**。

### Step 2: 验证「去重失败」不是 graph 代码失败

`interp_graph.filter_abnormal`(387 行):

```python
abnormal = [{**j, ...} for j in state["judgments"]
            if j["color_level"] in ("red", "yellow")]
```

——对每条 red/yellow judgment 各产一条,**没有去重逻辑,也不需要做**,因为按设计每个 judgment 对应一个 indicator row。`run_rules`(316 行)同理,`for ind in state["indicators"]` 对每条 indicator 产一条 judgment,忠实于 DB。

整条链上**没有一处是"去重失败"**——是从来没有任何环节做过去重,源头就是 `report_indicator` 表的多行同名同值。最自然的修复位置不应在 graph 里,而是在 `_parse_text_with_llm` 之后、`db.add` 之前的 `normalize_indicators`。

## 4. 根因

| 层次 | 根因 |
|---|---|
| 即时原因 | `normalize_indicators` 只做名称标准化,不做去重;`for ind in indicators: db.add(...)` 直接全量入库 |
| 二级缺陷 | LLM 抽取 prompt 没要求"跨章节同名同值指标去重",而体检 PDF 的章节结构必然导致 reprint |
| 关联问题 | agent_search_knowledge 因 names 列表带重复,多发 N 倍 search_knowledge 调用(本次 N=2);judge 也会重复审同一指标的同一句话;前端红黄绿计数被 inflate |

## 5. 修复

### 修复 A:在 `normalize_indicators` 加按 `(item_name_standard 或 item_name, result)` 的去重

`backend/app/core/term_normalizer.py`:

```diff
 def normalize_indicators(indicators: list[dict]) -> list[dict]:
+    """名称标准化 + 去重。
+
+    体检 PDF 通常在多个章节(主检报告 / 医学科普 / 分项报告)逐一列出同一指标的同一
+    数值;LLM 抽取时按章节各返回一条,DB 入库后会出现同名同值的多行。run_rules →
+    filter_abnormal 会忠实于 DB 行数,导致 agent_search_knowledge 收到重复指标名、
+    发重复 search_knowledge 调用、judge 也对重复指标重复审核。在此按
+    (item_name_standard 或 item_name, result) 去重,保留首次出现,顺序不变。
+    """
     for ind in indicators:
         name, code = normalize_item_name(ind.get("item_name", ""))
         ind["item_name_standard"] = name
         ind["item_code"] = code
-    return indicators
+
+    seen: set = set()
+    deduped: list[dict] = []
+    for ind in indicators:
+        key = (
+            ind.get("item_name_standard") or ind.get("item_name", ""),
+            str(ind.get("result", "") or "").strip(),
+        )
+        if key in seen:
+            continue
+        seen.add(key)
+        deduped.append(ind)
+
+    if len(deduped) != len(indicators):
+        logger.info(
+            "normalize_indicators deduped %d -> %d (dropped %d duplicates)",
+            len(indicators), len(deduped), len(indicators) - len(deduped),
+        )
+    return deduped
```

去重 key 用 `item_name_standard` 优先,确保"血糖"、"葡萄糖"映射到同一标准名后也算作同一指标;同时 `result` 转字符串 strip 空白,避免 `None` 和 `""` 的边界差异。同名不同值(如尿酸 431 vs 414)不合并,保留多次检查的真实变化。

## 6. 涉及文件列表

| 文件 | 改动 |
|---|---|
| `backend/app/core/term_normalizer.py` | `normalize_indicators` 末尾追加按 `(item_name_standard 或 item_name, result)` 去重逻辑 + dedup 日志 |
| `backend/tests/core/test_term_normalizer.py` | 新增 8 个测试:名称标准化 + 同名同值合并 + 同名不同值保留 + 按 standard 名合并 + 空 result 合并 + 顺序保持 + 无重复时不变 |
| `backend/tests/core/__init__.py` | 新增空 init |

## 7. 修复后验证

- pytest 全套 **102 passed**(新增 8 个,不破现有)。
- 同一 PDF 重新上传 → task_id=27:
  - `report_indicator` 共 **57** 条,`COUNT(DISTINCT CONCAT(item_name, '\t', result_value)) = 57`,**零重复**;
    (修前 task_id=25 共 60 条,3 条同名同值 duplicate;
    即 LYM%、NEUT%、ALT 三项各从 2 row 合到 1 row)。
- interpretations/27: yellow_count=3(原 6),red_count=0,green_count=54;
  judge user prompt 的异常指标列表只有 **3 条**:- 淋巴细胞百分数 / - 中性粒细胞百分数 / - 血清丙氨酸氨基转移酶。
- search_knowledge 查询次数同步减半(非直接测量,但 batch_size=10 内查询次数正比于 names 长度)。

## 8. 经验

1. **"去重失败"通常不是"去重代码失败"——而是去重逻辑从不存在,源头数据自带重复**。在没用 SQL UNIQUE 约束、没在解析后 dedup 的链路里,DB 里同名同值多行是 LLM 抽取 multimodal 数据的常态,排查要先翻 `report_indicator` 表看源头而不是去翻 graph。
2. PDF 的章节结构天然导致同名指标 reprint(主检段 / 医学科普段 / 分项报告段),给 LLM 抽取 prompt 写"按 (item_name, result_value) 去重"也可行,但工程上把去重放在 `normalize_indicators` 比放在 prompt 里更稳——不依赖模型自觉。
3. 紧邻但未修的相关缺陷:即使去重把查询次数减半,本次解读**仍然 references=0**——因为 `search_knowledge` 用指标字面中文名("淋巴细胞百分数")当 query,知识库 KG 实体全是疾病名(痛风、高尿酸血症…),Milvus 文档库几乎是空的。对照测试:RAG 命中率
    - `淋巴细胞百分数` / `中性粒细胞百分数` / `血清丙氨酸氨基转移酶` → 各 **0** 条
    - `尿酸` / `痛风` → 各 **3** 条
    即换措辞(如"中性粒细胞减少")能命中疾病实体。**修复路径候选**:(A) 调 SEARCH_SYSTEM_PROMPT 让 LLM 改用"症状/偏离方向"作二次查询;(B) 在 `search_knowledge` 工具内部加中文指标→病症短语映射做 query 扩展;(C) 兜底同义词词典;(D) 给 Milvus 灌入化验指标解读文档。本条记录只留备忘,不动 RAG。
4. 修一个 bug 时尽量在同一次真实数据上跑端到端,能同时把"流量超标(6 项 vs 3 项)""引用为空""judge 慢"几个表层现象一并暴露,否则修完一个还会被另一个遮住真相。

# 不调用知识参考
原因：1、主依赖环境没有jieba，无法分词，导致关键词不合适；2、kg原本只对entity进行搜索，无法处理多跳搜索

---

# 2026-07-21 — 管理后台「团体分析」前端 3 连发(404 / 乱码 / 空白页)

## 1. 现象

用户访问管理后台「团体健康体检分析」页面,出现三个表象:

1. **重点人群 tab 报 "Request failed with status code 404"**
2. **概览图一直转圈**(切到 overview tab 也没数据)
3. **概览图 X 轴第一医院名乱码**(`æ¼"ç¤ºåŒ»é™¢` 这种),且「医院」过滤下拉是空的

初看像是 3 个独立 bug,排查后发现 3 个层级各自独立的问题,但都属于"新加路由没贯通 / 旧数据 seed 写崩 / Vite resolve 取错文件"的历史欠账,挤在同一次访问里同时爆出来。

## 2. 链路梳理

```
后端末注册路由:
  backend/app/main.py:48
    app.include_router(statistics_group_router, prefix="/api/v1/statistics")
  ← 这是 2026-07 新增,但运行中的 uvicorn 是 3 天前启动的旧二进制
  ← /api/v1/statistics/group/overview 和 /group/high-risk 在 OpenAPI 里完全不存在

医院下拉空 + 乱码:
  start.sh:130
    docker exec -i hospital-mysql mysql -uroot -proot hospital_template <<SQL
    INSERT INTO hospital_tenant ... VALUES ('H001', '演示医院', ...) ...
  ← mysql CLI 连接未声明 --default-character-set=utf8mb4
  ← "演示医院" UTF-8 字节被服务端按 latin1 接收再以 utf8mb4 存储 → 双重编码乱码
  DB 实际存: æ¼"ç¤ºåŒ»é™¢  hex: C3A6C2BCE2809DC3A7C2A4C2BA...

  ↓ 於是 group_service._get_tenant_names() 读出来的 hospital_name 就是乱码
  ↓ 概览图 X 轴显示乱码

  FilterBar.tsx 原来做的是:
    <Select mode="tags" placeholder="留空=全部"
      value={value.hospital_ids || []}
      onChange={v => onChange({ ...value, hospital_ids: v as string[] })}
      style={{ minWidth: 200 }} />
   ← 没有 options,本质是自由 tag 输入,用户得手敲 hospital_id 字符串才能加 tag
   ← 后端没提供 GET /api/v1/tenants list 接口,前端也填不出下拉数据
   ← 综合结果:下拉永远是空,用户完全不知道有哪些医院可选
```

## 3. 排查过程(systematic-debugging)

### Step 1: 404 → openapi.json 是真理

```bash
curl http://localhost:8000/openapi.json | python3 -c "
import sys,json; d=json.load(sys.stdin);
print([p for p in d['paths'] if 'group' in p])
"
# 输出: [] ← group/overview、group/high-risk 都没有
```

`ps -eo pid,etime,cmd | grep uvicorn` 显示进程 etime=3 天前 → 进程是统计 group 路由合入 main.py 之前的版本。**衍生修复**:重启后端。

### Step 2: 乱码 → DB 直读 HEX 是真理

```python
r = db.execute(text("SELECT hospital_id, hospital_name, HEX(hospital_name)
                    FROM hospital_tenant WHERE hospital_id='H001'")).fetchone()
# id: H001  name: æ¼"ç¤ºåŒ»é™¢  hex: C3A6C2BCE2809DC3A7C2A4C2BAC3A5C592C2BBC3A9E284A2C2A2
```

经典 UTF-8 经 latin1 错读再以 utf8mb4 存的双重 mojibake。H002 "第二医院" 正常,说明是首启时 seed 写入烂尾。验证恢复路径:`bad.encode('cp1252').decode('utf-8')` → `演示医院` ✓。

### Step 3: 医院下拉空 → 没有 GET list 接口

`/api/v1/tenants` 在 OpenAPI 里只有 POST 注册新 tenant,没有 admin 拿列表的 GET 接口。FilterBar 用 `mode="tags"` 且无 `options`,.dropdown 也不会自己变出医院。**衍生修复**:新增 GET 路由 + FilterBar 拉数据填 options。

### Step 4: 重启 + 后端 GET 接口实现完成,前端硬刷仍空白

浏览器 Console:
```
Uncaught SyntaxError: The requested module '/src/api/groupAnalysis.js'
  does not provide an export named 'listTenants' (at FilterBar.tsx:4:10)
```

`ls admin-portal/src/api/` 看到 `groupAnalysis.js` 和 `groupAnalysis.ts` **同时存在**。Vite `resolve.extensions` 默认顺序是 `['.mjs', '.js', '.ts', '.jsx', '.tsx', '.json']`,`.js` 优先级高于 `.ts`,拿了不带 `listTenants` 的旧 `.js` → 死。

`git ls-files admin-portal/src/api admin-portal/src/stores` 显示只有 `.ts` 入 git,`.js` 是没进版本控制的历史残留(可能是更早期 build 工具或 babel 留下的)。

删 `.js` 后再硬刷,仍空白:
```
GET /src/stores/adminStore.js?t=1784676268762 net::ERR_ABORTED 404 (Not Found)
```

——Vite 的 `node_modules/.vite/deps/` 缓存里还存着对已删除 `.js` 的引用。清这两个目录、直接杀进程再用 vite 二进制重启(不能用 `pnpm dev`,会撞 `@hospital/shared` 找不到的 404,见 Step 5)。刷新后才真渲染。

### Step 5: 不能用 `pnpm dev` / `npm run dev` 启动 admin-portal

```
[ERR_PNPM_FETCH_404] GET https://registry.npmjs.org/@hospital%2Fshared: Not Found - 404
This error happened while installing a direct dependency of
  /data/project/hospitalKnowledgeBase/frontend/packages/admin-portal
```

`@hospital/shared` 是 workspace-local 包,只在 monorepo 内可解析;pnpm 6+'s "deps status check" 启动前会去公网拉,失败就 abort。`start_front.sh` 用 `nohup npm run dev -w @hospital/admin-portal` 也会触发同样的问题(`npm` 把工作区包名当公网 dep 检查)。**正解**:直接调 vite 二进制:
```bash
cd frontend/packages/admin-portal
/data/project/hospitalKnowledgeBase/frontend/node_modules/.bin/vite --port 3003
```
绕开包管理器的 deps verify,直接跑 vite 即可。`start_front.sh` 现存写法有这个隐藏炮,记录待改。

## 4. 根因总结

| 现象 | 根本原因 |
|---|---|
| 重点人群 404 | 后端进程没重启,新加的 `/api/v1/statistics/group/*` 路由未加载 |
| 概览图一直转圈 | UI 把"空 data"和"loading"都渲染成 `<Spin />`;实际是用户没点「查询」也不自动 submit,默认占位看起来像加载中;404 修好后真实请求也会因新加 admin 接口鉴权问题显示错误 alert |
| 概览 X 轴乱码 | `start.sh` 用 `mysql -uroot -proot` 通过 docker exec 灌 "演示医院",未声明 `--default-character-set=utf8mb4`,服务端按 latin1 接 UTF-8 字节后以 utf8mb4 存储 → 双重乱码 |
| 医院下拉空 | 后端无 GET list tenants 接口;FilterBar 用 `mode="tags"` 无 options,只能自由键入 hospital_id 字符串 |
| 硬刷依旧空白 | `admin-portal/src/api/groupAnalysis.js` 和 `.ts` 同存,Vite `resolve.extensions` 默认先取 `.js` 拿到旧文件,旧文件没 `listTenants` 导出 |
| 删 .js 仍空白 | Vite `node_modules/.vite/deps/` 缓存仍指已删除 `.js`;且 `npm run dev` 走 pnpm deps verify 撞 `@hospital/shared` 公网 404 直接 abort |

## 5. 修复

### A. 修复 DB 已存的乱码(不重 seed)

```python
from app.core.database import get_session
from sqlalchemy import text
db = get_session('hospital_template')
r = db.execute(text("SELECT hospital_name FROM hospital_tenant WHERE hospital_id='H001'")).fetchone()
recovered = r[0].encode('cp1252').decode('utf-8')   # 双重 mojibake 反推
db.execute(text("UPDATE hospital_tenant SET hospital_name=:n WHERE hospital_id='H001'"), {'n': recovered})
db.commit()
# 验证: SELECT hospital_id, hospital_name FROM hospital_tenant → '演示医院' / '第二医院' ✓
```

### B. start.sh 给 mysql CLI 加 charset

```diff
- docker exec -i hospital-mysql mysql -uroot -proot hospital_template <<'SQL' 2>/dev/null || true
+ docker exec -i hospital-mysql mysql -uroot -proot --default-character-set=utf8mb4 hospital_template <<'SQL' 2>/dev/null || true
  INSERT INTO hospital_tenant (hospital_id, hospital_name, db_name, is_active)
  VALUES ('H001', '演示医院', 'hospital_H001', 1)
  ON DUPLICATE KEY UPDATE hospital_name=VALUES(hospital_name);
  SQL
```

防 `bash start.sh` 全新部署时 seed 再写崩。其它 mysql CLI 调用点也都是同一类隐患,只在写入含中文的那条加了;后续若有其它 seed 含中文再逐一加。

### C. 后端新增 GET /api/v1/tenants(admin)

- `app/modules/tenant/schemas.py`: 加 `TenantListItem` / `TenantListResponse`
- `app/modules/tenant/service.py`: 加 `list_tenants(template_db, active_only=True)` 查表返回
- `app/modules/tenant/router.py`: 注册 `@router.get("", response_model=TenantListResponse)`,鉴权用 `Depends(require_role("admin"))`,支持 `active_only` 参数

```bash
JWT=$(curl -sX POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin1","password":"123456"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -m5 http://localhost:8000/api/v1/tenants -H "Authorization: Bearer $JWT"
# {
#   "items": [
#     {"hospital_id":"H001","hospital_name":"演示医院","is_active":1},
#     {"hospital_id":"H002","hospital_name":"第二医院","is_active":1}
#   ],
#   "total": 2
# }
```

### D. 前端 FilterBar 医院下拉接通

`frontend/packages/admin-portal/src/api/groupAnalysis.ts` 新增 `listTenants(activeOnly=true)` 和 `TenantItem` / `TenantListResponse` 类型。

`frontend/packages/admin-portal/src/pages/group-analysis/components/FilterBar.tsx`:

```diff
- import { Form, Select, DatePicker, Radio, Input, Button } from "antd";
- import type { GroupBy } from "../../../api/groupAnalysis";
- import dayjs from "dayjs";

+ import { useEffect, useState } from "react";
+ import { Form, Select, DatePicker, Radio, Input, Button } from "antd";
+ import type { GroupBy } from "../../../api/groupAnalysis";
+ import { listTenants, TenantItem } from "../../../api/groupAnalysis";
+ import dayjs from "dayjs";

  export default function FilterBar(...) {
+   const [tenants, setTenants] = useState<TenantItem[]>([]);
+   const [tenantsLoading, setTenantsLoading] = useState(false);
+   useEffect(() => {
+     let alive = true;
+     setTenantsLoading(true);
+     listTenants(true)
+       .then(items => { if (alive) setTenants(items); })
+       .catch(() => { if (alive) setTenants([]); })
+       .finally(() => { if (alive) setTenantsLoading(false); });
+     return () => { alive = false; };
+   }, []);
    ...
    <Form.Item label="医院">
-     <Select mode="tags" placeholder="留空=全部"
-       value={value.hospital_ids || []}
-       onChange={v => onChange({ ...value, hospital_ids: v as string[] })}
-       style={{ minWidth: 200 }} />
+     <Select
+       mode="multiple" placeholder="留空=全部"
+       showSearch optionFilterProp="label"
+       loading={tenantsLoading}
+       value={value.hospital_ids || []}
+       onChange={v => onChange({ ...value, hospital_ids: v as string[] })}
+       options={tenants.map(t => ({
+         value: t.hospital_id,
+         label: `${t.hospital_name} (${t.hospital_id})`,
+       }))}
+       style={{ minWidth: 240 }} />
    </Form.Item>
```

### E. 删除 admin-portal 内残留的 .js(让 Vite 只用 .ts)

```bash
rm frontend/packages/admin-portal/src/api/groupAnalysis.js
rm frontend/packages/admin-portal/src/stores/adminStore.js
# 这两份 .js 都是没进 git 的历史残留,内容与同名 .ts 重复但配置旧
```

### F. 清 Vite deps 缓存 + 二进制直跑

```bash
pkill -f "vite --port 3003"
rm -rf frontend/packages/admin-portal/node_modules/.vite frontend/node_modules/.vite
cd frontend/packages/admin-portal
/data/project/hospitalKnowledgeBase/frontend/node_modules/.bin/vite --port 3003
```

不能用 `pnpm dev` / `npm run dev -w @hospital/admin-portal` 启动 admin-portal:pnpm 启动前的 "verify deps" 会去公网拉 `@hospital/shared`(workspace-local 包),404 后直接 abort,即便 `node_modules` 已经装好。这也意味着 `start_front.sh:93` 的 `nohup npm run dev -w ...` 在脏环境下也会失败 —— 若 `npm` 走到 pnpm/verify 分支就中招。正解是直接调 vite 二进制(绕开 verify),或 `pnpm install` 在 monorepo root 一次性把 workspace 链接完后再 `pnpm dev`。

## 6. 涉及文件列表

| 文件 | 改动 |
|---|---|
| `backend/app/modules/tenant/schemas.py` | 加 `TenantListItem` / `TenantListResponse` |
| `backend/app/modules/tenant/service.py` | 加 `list_tenants(template_db, active_only)` 查 `hospital_tenant` |
| `backend/app/modules/tenant/router.py` | 注册 `GET /api/v1/tenants`,鉴权 `require_role("admin")`,支持 `active_only` 查询参数 |
| `backend/app/modules/statistics/group_router.py` | (无改动,但路由靠后端重启生效) |
| `frontend/packages/admin-portal/src/api/groupAnalysis.ts` | 新增 `listTenants()` API + `TenantItem` / `TenantListResponse` 类型 |
| `frontend/packages/admin-portal/src/pages/group-analysis/components/FilterBar.tsx` | 挂载时拉 tenant 列表 → `mode="multiple"` + `options` + `showSearch` |
| `frontend/packages/admin-portal/src/api/groupAnalysis.js` | **删除**(与 .ts 重复,使 Vite resolve 走 .ts) |
| `frontend/packages/admin-portal/src/stores/adminStore.js` | **删除**(同上) |
| `start.sh` | 给 seed `INSERT INTO hospital_tenant` 的 mysql CLI 加 `--default-character-set=utf8mb4` |
| DB `hospital_template.hospital_tenant` | UPDATE H001 修乱码 name → "演示医院" |

## 7. 修复后验证

- 后端重启后 `/openapi.json` 包含 `/api/v1/statistics/group/high-risk` 与 `/group/overview` ✓
- `curl /api/v1/tenants` 返回 `[{H001 演示医院}, {H002 第二医院}]` ✓
- `curl /api/v1/statistics/group/overview?group_by=hospital` 返回 2 行,labels 已是正确中文 ✓
- `tsc --noEmit` admin-portal 全通过(新加类型无报错)✓
- 删 `.js` + 清 `.vite/deps` + vite 二进制直跑后,浏览器硬刷新:`/src/stores/adminStore` 解析为 `.ts`(返回含 `admin_token` 的正确代码),`/src/api/groupAnalysis.ts` 含 `listTenants` ✓
- 前端页面能正常进入,FilterBar「医院」下拉展示 `演示医院 (H001)` / `第二医院 (H002)`,概览图 X 轴正确显示中文医院名 ✓

## 8. 经验

1. **OpenAPI schema 是排查路由 404 的真理**:别猜哪个路由有没有,直接 `curl /openapi.json` 看 paths 数组。后端代码里 `app.include_router(...)`,如果运行中的进程是更早的二进制,路由其实就没生效——重启进程才能让动态注册可见。
2. **MySQL CLI 写中文必带 `--default-character-set`**:`docker exec mysql -uroot -proot <SQL` 这种裸用法,连接默认 charset 跟 `my.cnf` 配置和服务端 `character_set_server` 都不一定一致;尤其容器化场景默认 latin1 仍居多。给所有写入含非 ASCII 的 seed SQL 加 `--default-character-set=utf8mb4`,比日后排"为什么 name 是乱码"工效高得多。
3. **诊断 mojibake 用 HEX 直读 + cp1252 反推**:DB 里看出乱码时,先 `HEX(col)` 拿真实字节,再决定编码方向。双重 mojibake(UTF-8 → latin1 → utf8mb4)的恢复路径是 `bad.encode('cp1252').decode('utf-8')`;一次性乱码 latin1 接 UTF-8 是 `bad.encode('latin1').decode('utf-8')`。验证恢复成功再 UPDATE,避免把数据改成又一种乱码。
4. **Vite resolve.extensions 默认 `.js` 优先于 `.ts`**:`admin-portal` 残留的 `.js`(没入 git)蔽屏了新的 `.ts`,导致加了类型还是报"无此导出"。Monorepo + 历史多人编辑 + 不同模板工具(babel/tsc/vite)留下的 `.js` 副本很常见,排查前端"模块找不到导出"时先 `ls` 是否有同名 `.js` 跟 `.ts` 共存。
5. **pnpm/npm "verify deps" 是 monorepo 隐藏炮**:workspace-local 包(如 `@hospital/shared`)在公网 registry 不存在,启动前的 deps status check 一旦走包管理器(pnpm dev / npm run dev -w)就会 404 abort,即便 `node_modules` 装好了。直接调 vite 二进制可绕开验证。`start_front.sh` 的启动方式要复查,确保 `pnpm install` 在 root 一次性把 workspace link 建好后再走 `pnpm dev`,或者直接调二进制。
6. **Vite deps 缓存 `.vite/deps/` 不随源码变动失效**:删完残留 `.js` 后,缓存里的模块仍可能指已不存在的路径。改 imports 路径或删同名文件时,顺手 `rm -rf node_modules/.vite` 清掉 dev server 的预打包缓存,否则浏览器会拿到旧的预打包版本报 404。
7. **UI loading/empty 不分**:OverviewCharts `"if (loading || !data) return <Spin />"` 把"加载中"和"未查询"渲染成同一种 spinner,用户分不清是"在跑"还是"待点查询"。后续改造:`!data && !loading` 显示 `<Empty description="请点击查询" />`,`loading=true` 才 Spin;并可在 GroupAnalysisPage 挂载时自动 submit 一次默认查询,行为与重点人群 tab(HighRiskTable 用 useEffect + load 自动取数)对齐。这次未做,留待后续体验优化。