# 批量上传体检报告 — 后续待办与部署注意

> 接手 Agent 必读: 本分支已交付 18 commit (`bd3f23e..9ba8aaa8`) 实现批量上传 zip/tar 包并自动解析+解读的能力,166 tests passing。下面按重要度排序列出**未做事项**与**部署/运维注意**,请按清单处理。
>
> 设计文档:`docs/superpowers/specs/2026-07-15-batch-report-upload-design.md`
> 实现计划:`docs/superpowers/plans/2026-07-15-batch-report-upload.md`
> SDD ledger:`.superpowers/sdd/progress.md`
> Final review:`.superpowers/sdd/final-review.md`
> C1-C3 fix report:`.superpowers/sdd/fix-c1-c3-report.md`

---

## 一、必须修复的遗留(Important Non-Blocking)

### 1. `retry_failed` 不区分 parse-stage 与 interp-stage 失败 [重要]

**位置**:`backend/app/modules/report/batch_service.py` `BatchService.retry_failed`

**现状**:`POST /api/v1/reports/batches/{id}/retry` 把所有 `failed` 行的 task 重置 `retry_count=0` + 重投 `parsing.bulk`。如果一个文件其实已经 parse OK,只是 interp 失败进了 DLQ,retry 会从头跑 OCR + `_parse_text_with_llm`(浪费 MedGo 调用),然后才再跑 interp。**且**它只重置 `ReportTask.retry_count`,不动 `ReportInterpretation` 行,可能留下 stale interp 状态。

**修复建议**:
- 在 `BatchImportFile` 加一个 `failed_stage` 字段(`"parsing"` / `"interpretation"` / `"oversize"`),由 `increment_progress("failed", stage=...)` 在写入时记录。
- `retry_failed` 根据 `failed_stage` 分发:
  - `"parsing"` 或 `"oversize"` → 重投 `parsing.bulk`(现状行为)
  - `"interpretation"` → 重投 `interpretation.bulk`,且**不清零** `ReportTask.retry_count`(parse 仍然 OK),只重置 `ReportInterpretation.status='pending'` 与 `retry_count=0`。
- 加 `BatchImportFile.failed_stage` 的 DDL:`ALTER TABLE batch_import_file ADD COLUMN failed_stage VARCHAR(24) DEFAULT NULL`(同时加到 `start.sh` DDL 块尾、`batch_models.py` 字段、init block)。
- 加测试:`test_retry_failed_interp_stage_routes_to_interp_bulk`。

**参考**:Final review `final-review.md` 的 I3 finding。

---

## 二、应该补的(Minor 但应做)

### 2. `scripts/bench-batch.sh` 的 API 形状与实际实现不符 [Minor]

**位置**:`scripts/bench-batch.sh`

**现状**:对 `/api/v1/reports/batches` 用 JSON body(`Content-Type: application/json`),chunk 上传用 header `X-Chunk-Seq`/`X-Total-Chunks` 与路径 `/batches/$BATCH_ID/chunks`(复数)。

**实际实现**(`batch_router.py`):
- `POST /api/v1/reports/batches`:multipart **Form** 字段 `filename`
- `POST /api/v1/reports/batches/{batch_id}/chunk`(单数):multipart `index`,`total`,`data:UploadFile`
- `POST /api/v1/reports/batches/{batch_id}/complete`:JSON `{expected_crc32?, expected_total, expected_size}`

**修复**:让脚本对齐实现,补上 `JWT=${JWT:-REPLACE_WITH_ADMIN_JWT}` 头与正确的字段名。脚本只需要 skeleton-aligned,不必真跑通。

---

### 3. `archive_too_large` 在 multi-tenant 部署下只跑 H001 [Minor]

**位置**:`start.sh` DDL 块

**现状**:新增的 `CREATE TABLE IF NOT EXISTS batch_import` / `batch_import_file` 两表 DDL 只写在 `start.sh:99-134` 现有的 H001 per-hospital init 块里。新加 tenant 的 DB 不会自动获得这两张表。

**影响**:未来加 tenant 的人必须知道要把这两段 DDL 也复制过去(其它老表同此限制,只是新表没人会想到)。

**修复建议**:在 `start.sh` DDL 块附近加一行 `# NOTE: 新 tenant init 必须包含 batch_import / batch_import_file 两表`,或在 `AGENTS.md` 末尾加一条"新 tenant 初始化"小节列出所有表名清单。

---

### 4. `BatchSweeper._hospital_ids` 硬编码 `("H001",)` [Minor]

**位置**:`backend/app/core/batch_sweeper.py`

**现状**:已有 `# TODO: replace with a hospital registry when multi-tenant sweep is needed` 注释。多 tenant 部署后,其它 tenant 的 stuck batch 不会被巡检。

**修复建议**:从 `dispatch` 模块的 hospital 列表来源(若有)取 hospital_ids 动态查询;或扫 `$FILE_STORAGE_ROOT/<hospital_id>/batch/` 目录推导 hospital_ids。

---

### 5. `batch_sweeper.py` 启动后如果 `get_hospital_db("H001")` 抛异常,sweeper 死掉 [Minor]

**位置**:`backend/app/core/batch_sweeper.py` `_sweep_once`

**现状**:外层 `for hospital_id in ("H001",):` 与 `db = next(...)` 包在 try/except 里,但 `await asyncio.sleep` 之间的 loop 异常如果发生在 `BatchService._maybe_advance_status` 内部仍会传到外层 `start()`,被 `except Exception: log.exception` 捕获后继续 sleep——所以实际是健壮的。**仅建议**:加一条 `sweeper_task.add_done_callback` 在 `main.py` 启动处,如果 sweeper task 异常退出打 error 日志。

---

## 三、部署与运维必读(OPSCritical)

### 6. RabbitMQ 队列声明是单向迁移,部署前**必须**删旧队列 [OPSCritical]

**原因**:新增 `x-dead-letter-exchange=hospital.dlx` args 改了 6 个原队列声明的 args。RabbitMQ 在 `queue_declare` 时 args 与现存队列不一致 → `PRECONDITION_FAILED` 通道关闭。

**部署步骤**:
```bash
bash stop.sh
bash infra/rabbitmq-queue-reset.sh   # 删 5 个旧队列
bash start.sh                        # 新代码 queue_declare 创建带 DLX 的新队列
```

**`infra/rabbitmq-queue-reset.sh` 删的旧队列**:
- `parsing.urgent`, `parsing.normal`
- `interpretation.urgent`, `interpretation.normal`
- `dead.letter`

**新增的 7 个主队列 + 7 个 retry 队列** 由 `rabbitmq.py:_ensure_resources` 在首次启动时自动声明,无需手动。

**回滚注意**:回滚到旧代码时**不能**直接 `git checkout && start.sh` —— 旧代码 `queue_declare` 不传 args,现存队列已有 args → 仍 `PRECONDITION_FAILED`。回滚必须同时**重删带 DLX 的新队列**(扩展 `rabbitmq-queue-reset.sh --revert` 或手动 `rabbitmqctl delete_queue` 所有 14 个新队列)。

---

### 7. 部署前跑一次完整 pytest + import sanity [OPSCritical]

```bash
cd /data/project/hospitalKnowledgeBase/backend && .venv/bin/pytest tests/ -q
# Expected: 166 passed, 0 failed
```

参考基线:`9ba8aaa8` 处的 suite 是 166 passed。

---

### 8. 启动时 worker 进程数现已变成 3 [OPSCritical]

`start.sh` 现在启动 3 个独立 Python 进程:
- `app.modules.report.worker`(`parsing.urgent` + `parsing.normal` + `parsing.bulk`)
- `app.modules.report.extract_worker`(`extract.bulk`)
- `app.modules.interpretation.worker`(`interpretation.urgent` + `interpretation.normal` + `interpretation.bulk`)

cleanup trap 增加了 `pkill -f "app.modules.report.extract_worker"`,确认 stop.sh / 信号处理覆盖到。

GPU 占用未变(extract worker 不抢 GPU)。

---

### 9. `MEDGO_MAX_CONCURRENCY` 调参 [OPSCritical]

**默认**:`MEDGO_MAX_CONCURRENCY=2`

**作用**:`medgo_sem` 单 `asyncio.Semaphore`,所有 MedGo 调用点(chat、report、interp、judge、planner、ai-summary)的总并发硬上限。

**调参建议**:
- 先用默认 2 上线,观察 chat 流式响应延迟 + 显存曲线
- 如果 MedGo 在 4 卡 TP=4 下显存余量大,可调到 3
- 如果 chat 卡顿明显(尤其流式时段的 sem 占用),回想 reviewer Concern #3:`async with medgo_sem:` 贯穿整个流式输出(可能数十秒),低 N 下 chat 显著降速——必要时考虑 chat 路径独立 sem 配额或改用 vLLM `--max-num-seqs` 双保险(见下)

**不要**直接拉到 8+;vLLM `--max-num-seqs` 默认 256,与 sem 不对齐,瞬间涌入大量并发可能占爆 KV cache。

---

### 10. vLLM `--max-num-seqs` 加固(可选但强推荐) [OPSCritical]

**位置**:`start.sh:155-159` 的 vllM 启动命令

**建议**:加 `--max-num-seqs 4`,与应用 `MEDGO_MAX_CONCURRENCY=2` 双保险:
- 应用 sem 限外部并发 ≤ 2
- vLLM 内部 max-num-seqs=4,即使 sem 失效也只准备 4 个 slot,绝不可能瞬间涌入 256 并发打爆 KV cache

**成本**:需要重启 MedGo vLLM,约 40s 停机。第一次部署时一起做。

---

### 11. 死信队列 7 天 TTL [运维注意]

**位置**:`rabbitmq.py:_ensure_resources` `DLQ_ARGS={"x-message-ttl": settings.DEAD_LETTER_TTL*1000}`(默认 604800s=7天)

**含义**:`dead.letter` 队列中消息 7 天后自动 drop。运维**必须**在 7 天内通过 `GET /api/v1/reports/batches/{id}/dead` + `POST /api/v1/reports/batches/{id}/retry` 处理死信,否则丢消息。

如需更长保存期:调大 `DEAD_LETTER_TTL` env。注意盘占用。

---

### 12. Bulk 时段窗口默认 22:00-08:00 [运维注意]

**位置**:`BULK_WINDOW_START=22` / `BULK_WINDOW_END=8`(env;`retry.py:is_bulk_window_now` 读 `os.getenv`)

**含义**:`parsing.bulk` 与 `interpretation.bulk` consumer 仅在此时段消费。**窗口外**,bulk 队列堆积,worker `nack(requeue=True) + sleep(5)` 慢 ticker。

**调整**:按医院夜间空闲时段调整。跨午夜支持(start > end)。

`extract_worker` **不受时段限制**(只读盘不调 MedGo),全时段消费。

---

## 四、已知限制 / 既有但非新增

### 13. `medgo_sem` 是 per-process,不是真全局 [理论限制]

**位置**:`app/ai/llm.py`

**含义**:`asyncio.Semaphore` 是 module-level 单例,每个进程独立持有。多进程 worker 部署下"全局 MedGo 并发" = `进程数 × MEDGO_MAX_CONCURRENCY`,而非真全局 N。

**当前部署**:每 worker 类型 1 进程(`start.sh`),1 backend uvicorn,加上 interp+parse+extract worker 共 4 个 Python 进程,但每个进程内只在自己 chat / interp 路径上 MedGo 调用——extract_worker 不调 MedGo。所以实际"会调 MedGo 的进程"只有 backend uvicorn + parsing worker + interp worker = 3 进程 × 2 = 6 并发上限。当前 vLLM GPU 余量足够,但单 vLLM 实例 4×L20 长上下文跑 6 路并发 push 上限时建议开 vLLM `--max-num-seqs` 加固。

未来若加多 worker 进程,需核算。

---

### 14. 跨批 crc32 去重复用未实现 [按 spec §3.4 故意不做,改 future]

**位置**:`app/modules/report/batch_service.py` `handle_extracted_file`

**spec 决定**:第一版只做批内 `(batch_id, crc32)` 唯一。同样的 PDF 在不同 batch 中会跨批重复解析。

**需要时**:改为查 `batch_import_file` 全表 by `crc32 + file_size` 命中则复用旧 `report_task_id` 短路。已留 future 钩子注释位置——**确认是否真的有注释,如没有补**:`handle_extracted_file` 内的 SELECT 加 `# FUTURE: global dedupe by crc32` 一行注释提醒后人。

---

### 15. `run_planner` 在 Task 4 被改成 `async def` [历史决定]

**位置**:`app/ai/agents/chat_planner.py`

**原因**:original brief 要求"sync → `asyncio.run`",但 `chat_graph.run_chat_agent` 是 async 调用 `run_planner`,在运行中的 event loop 里 `asyncio.run` 会抛 RuntimeError。

**调用方更新**:`chat_graph.py:267` 已改为 `plan = await run_planner(...)`。如果未来新增其它 sync 调用 `run_planner`,必须先 `asyncio.run` 或改成 sync 版本。

---

## 五、不要做的事(按照 spec/AGENTS.md)

| 行为 | 原因 |
|------|------|
| ❌ 把 `vllm` 加回 `backend/pyproject.toml` | 见 AGENTS.md:业务代码不 import vllm,vllm 由独立 venv `backend/.venv-vllm-cu12` 提供 |
| ❌ `cd backend && uv sync` 期望它装出能跑 vllm 的环境 | 它故意不装 vllm |
| ❌ 升 torch / 改 cu13 路径 | 本机驱动 535.247.01 / CUDA 12.2,apt 源顶天到 575(无 580+) |
| ❌ 删改 `start.sh` 里的 vLLM 启动参数 | 见 AGENTS.md GPU 分配 |
| ❌ 把 `medgo_sem` 改成 threading.Semaphore | 当前单 loop 假设 OK,见 final-review Concern #4;若未来多线程 worker 才需改 |
| ❌ 去掉 `chat_graph.py` per-session lock | `medgo_sem` 是全局闸,**per-session lock 保留**用于防同 session 双发 |

---

## 六、上手 checklist(给下个 agent)

1. 先读 final-review.md(了解 C1-C3 已修)+ fix-c1-c3-report.md(清楚改了什么、为什么)
2. `cd backend && .venv/bin/pytest tests/ -q` 必须绿(166 passed)
3. 跑一遍 `python -c "import app.main"` 检查 import sanity
4. 阅读本文件后决定优先级:必做 #1 → 部署前必跑 #7 → 下一个 Sprint #1

下个 sprint 建议批次:
- **P0**:#1 `retry_failed` 分 parse/interp 失败阶段(需要 DDL + 测试)
- **P0**:#6 #7 部署前流程
- **P1**:#9 调 `MEDGO_MAX_CONCURRENCY` 并观察生产显存
- **P1**:#10 vLLM `--max-num-seqs` 加固
- **P2**:#2 #3 #4 #5 Minor 收尾

---

## 七、参考索引

| 资源 | 路径 |
|------|------|
| 设计 spec | `docs/superpowers/specs/2026-07-15-batch-report-upload-design.md` |
| 实现计划 | `docs/superpowers/plans/2026-07-15-batch-report-upload.md` |
| SDD ledger | `.superpowers/sdd/progress.md` |
| Final review | `.superpowers/sdd/final-review.md` |
| Fix report (C1-C3) | `.superpowers/sdd/fix-c1-c3-report.md` |
| 项目工程记忆 | `AGENTS.md` |