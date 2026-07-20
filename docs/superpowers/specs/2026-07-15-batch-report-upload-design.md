# 批量上传体检报告解析 — 设计文档

- **日期**: 2026-07-15
- **状态**: Draft (待用户 review)
- **范围**: 新增批量上传体检报告功能,并在现有 RabbitMQ + vLLM 架构上做最小但有根因性的改造
- **非目标**: 不迁移 Celery / Redis-streams;不做分布式 worker 横扩;不做文件夹扫描第二种入口;不动 venvs / GPU 分配

---

## 1. 背景与已知风险

现状(见探索报告)对"批量上传 1000+ 份体检报告"存在 7 处风险:

| # | 风险 | 位置 | 严重度 |
|---|------|------|--------|
| 1 | 上传端把整个文件读进内存再校验大小 | `report/router.py:50-52` | 高 |
| 2 | MQ 没有真 DLQ,`dead.letter` 声明了但未绑 `x-dead-letter-exchange`,解析失败的批量消息被 `basic_nack(requeue=False)` 静默丢弃 | `rabbitmq.py:45,89` | 高 |
| 3 | DB 重试无延迟,`retry_count<3` 直接重投 `parsing.*` 队列,批量失败时形成重试风暴,瞬间打满 MedGo/BGE | `report/service.py:130-141` | 高 |
| 4 | interpretation worker 重试靠 `time.sleep(10/20s)` 阻塞唯一工作线程,批量场景下队列堆积、吞吐归零 | `interpretation/worker.py:63` | 中 |
| 5 | 全局对 MedGo 推理没有任何并发上限。worker 路径 `prefetch=1` 限到 1,但 chat 路径完全绕过 MQ,N 个 session = N 路并发 `model.astream`,叠加批量上传显存可能炸 | `chat_graph.py:286`、`llm.py` 全程无并发控制 | 高 |
| 6 | 只有 1 个 parsing worker + 1 个 interp worker,`prefetch=1`,整条流水线峰值并行度 = 1。削峰有了但填谷性能为零 | `worker.py` | 中 |
| 7 | DOCX 在白名单里但 worker 会直接抛异常,批量上传混入一个 docx 全批卡住 | `report/service.py:231` | 中 |
| 附 | PaddleOCR-VL 单进程 FastAPI + 图片逐页串行,无并发上限 | `paddle_ocr_service/main.py` | 中 |

需求边界(用户确认):
- **主场景**: 管理员批量导入历史档案(1000+ 份),次要场景多用户日常并发上传
- **入口**: zip/tar 单包上传
- **SLA**: 小时级,后台跑不需人等
- **范围**: 解析 + interp 全跑,但 interp 单独限流/限时段

## 2. 方案选择

三个候选方案对比:

- **方案 A (选定)**: `zip endpoint + 多优先级队列 + 修 DLQ/重试 + medgo_sem 收口`。复用现有 pika 阻塞 worker,在外围加东西。解决全部 7 处根因。工作量中等。
- **方案 B**: 引入 Celery / FastStream + Redis Streams。架构迁移代价大,收益相对 A 没有数量级提升。否决。
- **方案 C**: 只在前端 + 网关层做限流,后端不变。无法满足 zip/tar 单包入口,不解决根因。否决。

选 A 的工程价值:
1. `medgo_global_semaphore` 是根除「显存爆炸」的关键。vLLM 自身有 continuous batching + KV-cache OOM 自救,真正危险的是显存碎片 + 32K 长上下文 + max_tokens=16384 在 4 卡 util 0.6 下并发几十路。
2. 真 DLQ + 指数退避重试解决"重投风暴"。
3. 新增 `parsing.bulk` / `interpretation.bulk` 队列 + bulk consumer 在非黄金时段消费。
4. `batch_import` 表 + 流式分片上传满足千份 zip 包上传与进度跟踪。
5. 不动 venvs / start.sh 的 GPU 分配,只调 Python 代码,升级风险低。

## 3. 架构总览

### 3.1 数据流

```
admin client         backend (FastAPI :8000)            RabbitMQ                workers              vLLM/GPU
  |  POST /reports/batch-upload (admin)                                              |                       |
  |  zip/tar + manifest (multipart, 分片 5MB) ──► 流式分片接收 + 落盘 archive           |                       |
  |                                            (建 batch_import row)                |                       |
  |                                            publish extract.task ───────────────►|                       |
  |                                              (新轻量 extract worker, 不抢 GPU) |─ consume ─────────────►|
  |                                            extract worker 串行/分批解压                                    |
  |                                            对每份 caught 文件:                                    |
  |                                            update batch_import_file (queued)                              |
  |                                            publish parsing.bulk (idempotent) ─────────────────────────►|
  |                                                                                       |─ parsing 消费:bulk consumer 分时段|
  |                                                                                       |─ 走 OCR / _parse_text_with_llm ──►PaddleOCR-VL|
  |                                                                                       |   全部 MedGo call 过 medgo_sem ──┤
  |                                                                                       |─ 完成 → publish interpretation.bulk ►|
  |                                                                                       |─ interp bulk consumer 分时段         |
  |                                                                                       |   planner/agent/judge 也过 medgo_sem ──┤
  |                                                                                       |─ done → update batch_import counters  |
  |                                                                                       |                                       |
  | GET /reports/batches/{id} ──► 进度 + 失败列表 + 重试入口                                                          |
```

### 3.2 组件清单

| 单元 | 文件 | 职责 |
|------|------|------|
| C1 batch router | `app/modules/report/batch_router.py` (新) | 分片上传、创建 batch、查进度/死信、手动重试/取消(8 个 endpoint) |
| C2 batch service | `app/modules/report/batch_service.py` (新) | 状态机推进、幂等协调 |
| C3 extract worker | `app/modules/report/extract_worker.py` (新) | 解压 archive → 逐份 create_report_task,不抢 GPU |
| C4 batch models | `app/modules/report/batch_models.py` (新) + `start.sh` DDL | `batch_import` / `batch_import_file` 表 |
| C5 medgo sem | `app/ai/llm.py` (改) | `asyncio.Semaphore(N)` 单闸,async/sync 双路统一 |
| C6 chat 路径接入 sem | `chat_graph.py`、`user_profile/service.py` (改) | `async with medgo_sem:` 包裹 MedGo 调用 |
| C7 worker 路径接入 sem | `interp_graph.py`、`judge_graph.py`、`chat_planner.py`、`report/service.py` (改) | 同上 |
| C8 multi-queue + 真 DLQ | `app/core/rabbitmq.py` (改) | 新增 `parsing.bulk` / `interpretation.bulk` 队列;4+2 队列绑 `x-dead-letter-exchange`;DLQ 接管 |
| C9 retry with backoff | `app/core/retry.py` (新) + 改 service/worker | 重试指数退避,延迟队列周转,删除 `time.sleep` |
| C10 bulk consumer 时段限流 | 改 `worker.py` × 2 | bulk consumer 仅在 BULK_WINDOW 内消费,非窗口 `nack(requeue=True) + sleep(5)` |
| C11 BatchSweeper | `app/core/batch_sweeper.py` (新) | 后台协程 5min 周期,推进卡住的 batch |
| C12 测试 | `tests/` (新) | 见 §6 |

### 3.3 边界与隔离

- **batch router** 只做 HTTP 协议,不含解压逻辑;解压交给 worker (C3),保证 HTTP 线程不阻塞。
- **batch_service** 是状态机协调者:`uploading → extracting → parsing → interpreting → completed | partial_failed | cancelled`。
- **extract_worker** 与 parsing worker 资源隔离:extract 只读 FS + 发 MQ,不抢 GPU;可独立横扩。
- **medgo_sem** 是整个系统的硬并发上限,把所有 MedGo 调用收敛到一个闸门后。建议同时在 vLLM `--max-num-seqs 4` 加固(见 §5.4)。
- **DLQ**:`dead.letter` 由 `hospital.dlx` topic exchange 绑定 `routing_key=dead` 收尾;死信不再静默。

### 3.4 非目标 (YAGNI)

- 不做 Celery / Redis-streams 迁移。
- 不做分布式 worker 横扩部署(单机足够 SLA)。
- 不做"以文件夹方式扫描磁盘"第二种入口。
- 不动 vLLM 启动参数 / venvs / GPU 分配(可选加固除外)。
- 不重构现有 chat graph 的 RAG/Milvus 部分。
- 跨批 crc32 去重复用:第一版不做,留 `# FUTURE` 钩子。

## 4. 组件接口与数据契约

### 4.1 数据模型 (C4)

`batch_import` — 一次批量导入:

```python
class BatchImport(Base):
    __tablename__ = "batch_import"
    id              = Column(String(36), primary_key=True)   # uuid4
    hospital_id     = Column(String(32), nullable=False)
    user_id         = Column(String(64), nullable=False)
    filename        = Column(String(255), nullable=False)    # 原始包名 "2024_q3_import.zip"
    archive_path    = Column(String(512), nullable=False)    # FILE_STORAGE_ROOT/<h>/batch/<batch_id>.zip
    total           = Column(BigInteger, default=0)
    parsed_ok       = Column(BigInteger, default=0)
    interp_ok       = Column(BigInteger, default=0)
    failed          = Column(BigInteger, default=0)
    status          = Column(String(24), default="uploading")
    error_message   = Column(Text)
    created_at      = Column(DateTime, default=func.now())
    completed_at    = Column(DateTime)
    updated_at      = Column(DateTime, default=func.now(), onupdate=func.now())
```

状态机: `uploading → extracting → parsing → interpreting → completed | partial_failed | cancelled`

`batch_import_file` — 包内每份文件(幂等关键):

```python
class BatchImportFile(Base):
    __tablename__ = "batch_import_file"
    id             = Column(String(36), primary_key=True)
    batch_id       = Column(String(36), ForeignKey("batch_import.id"), nullable=False)
    file_path      = Column(String(512), nullable=False)     # 解压后相对路径
    file_size      = Column(BigInteger, default=0)
    crc32          = Column(String(8),  nullable=False, index=True)
    status         = Column(String(24), default="queued")
    report_task_id = Column(String(36))
    error_message  = Column(Text)
    created_at     = Column(DateTime, default=func.now())
    __table_args__ = (UniqueConstraint("batch_id","crc32", name="uq_batch_file"),)
```

DDL 增量加进 `start.sh:99-134`,非空时 `CREATE TABLE IF NOT EXISTS`。

### 4.2 HTTP 接口 (C1)

| 方法 | 路径 | 入参 | 出参 |
|------|------|------|------|
| POST | `/reports/batches` | multipart `filename`,`priority=bulk` | `201 {batch_id}` |
| POST | `/reports/batches/{batch_id}/chunk` | multipart `index,total,data:UploadFile` (5MB/片) | `200 {received, total}` — 流式 CRC32 累加,分片落 `.partN` |
| POST | `/reports/batches/{batch_id}/complete` | JSON `{expected_crc32?, expected_total, expected_size}` | `202 {batch_id, total_size}` — 校验 CRC → 拼装 `<batch_id>.zip` → status=`extracting` → publish `extract.task` |
| GET | `/reports/batches` | query `page,size,status` | 分页列表,admin only,按 hospital 过滤 |
| GET | `/reports/batches/{batch_id}` | — | `{...BatchImport, files: [...], summary: {...}}` |
| GET | `/reports/batches/{batch_id}/dead` | — | DLQ 内属于本 batch 的死信(消息体 batch_id header 过滤) |
| POST | `/reports/batches/{batch_id}/retry` | JSON `{file_ids?:[]}` 默认全失败 | `202 {requeued:N}` |
| POST | `/reports/batches/{batch_id}/cancel` | — | `200 {cancelled:true}` — 仅停止后续 publish,已 publish 的 task 仍运行 |

**权限**: 仅 role=admin 可调 (沿用 `get_current_user` JWT)。

**包约束**:
- 单包 ≤ `BATCH_ARCHIVE_MAX_SIZE` (env,默认 10GB),`complete` 末尾 size 校验
- 单片 5MB 允许断点续传,`index` 乱序允许,`complete` 按序拼装
- 客户端可省略 CRC,服务端 best-effort 跳过包级 CRC 只做分片 count 校验

### 4.3 BatchService (C2) 主要方法

```python
class BatchService:
    @staticmethod
    def create_batch(hospital_id, user_id, filename) -> BatchImport
    @staticmethod
    def append_chunk(batch_id, index, total, chunk_io_stream) -> int
    @staticmethod
    def finalize_batch(batch_id, expected_crc32, expected_total)
    @staticmethod
    def get_progress(batch_id) -> dict
    @staticmethod
    def publish_extract_task(batch_id)
    @staticmethod
    def handle_extracted_file(batch_id, file_path, crc32, size) -> str  # 幂等,返回 file_id 或 skip
    @staticmethod
    def increment_progress(batch_id, field)  # UPDATE...WHERE status NOT IN ('parsed','interp_ok')
    @staticmethod
    def retry_failed(batch_id, file_ids=None) -> dict
```

写入幂等用 `INSERT ... ON DUPLICATE KEY` 或 `UPDATE...WHERE status='queued'`。

### 4.4 extract_worker (C3)

`extract.task` 消息体: `{"batch_id": "...", "archive_path": "...", "expected_total": N}`

消费逻辑:
1. 拉消息,prefetch=1
2. 检查 `batch_import.status=='extracting'`,否则丢弃(cancelled)
3. 用 zipfile/tarfile 流式打开 archive;zip-bomb 防护:单个文件 size < `BATCH_FILE_MAX_SIZE` (50MB),总大小 ≤ 5× archive_size
4. 每个解压文件:
   a. 过滤扩展名 ∈ {pdf, doc, jpg, jpeg, png}(去掉 docx);path 过滤 `__MACOSX`/`.DS_Store`
   b. 计算 crc32(流式)
   c. `BatchService.handle_extracted_file(...)` 幂等去重,返回 `(file_id, is_new)`
   d. `if is_new: report_service.create_task(... priority=False, batch_id=batch_id)` -> report_task_id
   e. 更新 `BatchImportFile.status='queued', report_task_id, file_path`
   f. 每 100 件 flush + log 进度
5. 完成 → `batch_import.status='parsing', total=N`,ack
6. 0 件成功 → `status='partial_failed'`,error_message 说明
7. 异常 → publish `extract.task` 到延迟队列 or DLQ

### 4.5 medgo_sem (C5)

`app/ai/llm.py` 新增:

```python
import asyncio, os
_MEDGO_MAX = int(os.getenv("MEDGO_MAX_CONCURRENCY", "2"))
medgo_sem = asyncio.Semaphore(_MEDGO_MAX)

async def _guarded(coro):
    async with medgo_sem:
        return await coro
```

**统一方案**: 所有 MedGo 调用点(async/sync)统一走 `asyncio.Semaphore`:
- async 调用点: `async with medgo_sem: result = await model.ainvoke(...)`
- sync 调用点 (worker): `result = asyncio.run(_guarded(model.ainvoke(...)))`
  - worker 在独立线程内 `asyncio.run` 不会有嵌套 loop 问题
  - 每次 MedGo 调用上下文进入新事件循环开销可接受

**改动点**: 6 处(见 §5.1)
- `report/service.py` `_parse_text_with_llm`
- `interp_graph.py` ~3 处
- `judge_graph.py` ~1 处
- `chat_graph.py` ~2 处
- `chat_planner.py` ~1 处
- `user_profile/service.py` ~2 处(ai-summary)

### 4.6 RabbitMQ 队列改动 (C8)

`app/core/rabbitmq.py` 队列声明:

```python
EXCHANGE = "hospital.tasks"
DLX = "hospital.dlx"

QUEUES = {
    "parsing.urgent":         "parsing.urgent",
    "parsing.normal":         "parsing.normal",
    "parsing.bulk":           "parsing.bulk",
    "interpretation.urgent":  "interpretation.urgent",
    "interpretation.normal":  "interpretation.normal",
    "interpretation.bulk":    "interpretation.bulk",
}
# 按来源拆 retry 队列:每个 retry 队列只能配一个 DLX routing key,需分别声明
RETRY_QUEUES = {
    "parsing.urgent.retry":         "parsing.urgent",        # TTL 后回 urgent 队列
    "parsing.normal.retry":         "parsing.normal",
    "parsing.bulk.retry":           "parsing.bulk",
    "interpretation.urgent.retry":  "interpretation.urgent",
    "interpretation.normal.retry":  "interpretation.normal",
    "interpretation.bulk.retry":    "interpretation.bulk",
}
DEAD_LETTER = "dead.letter"

QUEUE_ARGS = {"x-dead-letter-exchange": DLX, "x-dead-letter-routing-key": "dead"}
DLQ_ARGS = {"x-message-ttl": 604800}  # 7 天

channel.exchange_declare(EXCHANGE, exchange_type="topic", durable=True)
channel.exchange_declare(DLX, exchange_type="topic", durable=True)
for rk, qn in QUEUES.items():
    channel.queue_declare(qn, durable=True, arguments=QUEUE_ARGS)
    channel.queue_bind(qn, EXCHANGE, routing_key=rk)
# retry 队列:声明时 x-dead-letter-exchange 指回主 exchange,TTL 后回对应的原始队列
for rk, target_rk in RETRY_QUEUES.items():
    args = {"x-dead-letter-exchange": EXCHANGE, "x-dead-letter-routing-key": target_rk}
    channel.queue_declare(rk, durable=True, arguments=args)
    channel.queue_bind(rk, EXCHANGE, routing_key=rk)
channel.queue_declare(DEAD_LETTER, durable=True, arguments=DLQ_ARGS)
channel.queue_bind(DEAD_LETTER, DLX, routing_key="dead")
```

`consume` 改为 per-queue `basic_consume`,每个队列独立 callback,便于单独加时段过滤。worker 一次启多消费者。

`consume_dead(batch_id)` 用 `basic_get` 拉不消费,消息体 `batch_id` header 过滤。

### 4.7 retry with backoff (C9)

`app/core/retry.py`:

```python
BACKOFFS = (10, 60, 600)  # 10s / 1m / 10m
def backoff_for_retry(retry_count: int) -> int:
    return BACKOFFS[min(retry_count, len(BACKOFFS)-1)]
```

发布延迟消息: publish 到对应来源的 retry 队列(如 `parsing.bulk.retry`),消息 `expiration = backoff_ms`,TTL 过期后 DLX 路由回原始来源队列(经典 rabbit delayed-message pattern,不需插件)。

**触发位置**: retry 不在 `report/service.py` 入队时触发,而是在 **worker 执行 MedGo/OCR 调用失败** 时触发:
- worker catch 单次调用异常 → 读消息体 `retry_count`
- `retry_count >= 3`: `nack(requeue=False)` → 走 DLQ
- 否则: publish 到**对应来源**的 retry 队列(原消息 routing_key ∈ {parsing.urgent, parsing.normal, parsing.bulk} 决定回到哪个 retry 队列),`expiration = backoff_ms`

`report/service.py:130-141` 原"立即重投 parsing.* "逻辑删除,改由 worker 上面这条流程处理;`service.py` 只负责 `nack(requeue=False)` 与写 `report_task.status='failed'`,不再做重投。

`interpretation/worker.py` 删除 `time.sleep` 退避块,改用同一套 retry 队列流程(消息 routing_key ∈ {interpretation.urgent, normal, bulk} 决定回到哪个 retry 队列)。

### 4.8 bulk consumer 时段过滤 (C10)

```python
def is_bulk_window_now() -> bool:
    start = int(os.getenv("BULK_WINDOW_START","22"))
    end   = int(os.getenv("BULK_WINDOW_END","8"))
    h = datetime.now().hour
    if start <= end: return start <= h < end
    return h >= start or h < end   # 跨午夜
```

bulk callback:
```python
def on_bulk(msg):
    if not is_bulk_window_now():
        basic_nack(delivery_tag, requeue=True)
        time.sleep(5)   # 5s 慢 ticker,只影响 bulk 队列(独立 channel)
        return
    handle(msg)
```

`extract_worker` **不**受时段限制(只读盘不调 MedGo),全时段消费。

### 4.9 BatchSweeper (C11)

后台 asyncio task (uvicorn worker 内):
- 每 `BATCH_SWEEP_INTERVAL`(300s)扫 `batch_import` 中 `status in ('extracting','parsing','interpreting')` 且 `updated_at < now - BATCH_SWEEP_STALL_THRESHOLD`(1800s) 的批
- 若 `total == parsed_ok + interp_ok + failed` → 自动推进状态
- `extracting` 卡住 → 幂等续跑(extract worker 已有去重)
- `cancelled` → 不推进

### 4.10 幂等与去重契约

1. **逻辑唯一键**: `(batch_id, crc32)`;`report_task.id` uuid4 独立
2. **重放安全**:
   - 重启 worker → 已完成消息已 ack,不重投
   - 管理员 retry → 显式重新 publish
   - archive 解压重投 → extract worker 起手 `SELECT count(*) FROM batch_import_file WHERE batch_id=?` 已有 ≥1 行时只补差,不重复 publish 已记账文件
3. **跨批去重**: 第一版不做,留 `# FUTURE` 钩子注释

## 5. 失败路径与失败语义

穷举失败路径,每条给触发层、后果、处理动作。

| # | 失败点 | 后果 | 处理 |
|---|------|------|------|
| F1 | 单包超 `BATCH_ARCHIVE_MAX_SIZE` | complete 阶段拒收 | 400 `{error:"archive_too_large"}`,分片清理,batch `cancelled` |
| F2 | 包级 CRC32 不匹配 | archive 损坏 | 400 `{error:"crc_mismatch"}`,status=`cancelled`,**不**删盘片,允许基于相同分片重发 complete |
| F3 | 分片重复上传(同 index) | 浪费磁盘 | 幂等覆盖:`.partN` 重写,manifest 累加去重 |
| F4 | 上传途中前端断连 | `.partN` 残留 | 后台 reaper 每 10min 扫 `status='uploading'` 且 `updated_at < now-2h` 的批,删分片 + 删行 |
| F5 | 解压时 zip/tar 损坏 | 中途崩溃 | catch `BadZipFile`/`TarError` → status=`partial_failed`,error_message,ack 消息(重投也救不回);已投出 task 继续跑 |
| F6 | zip 炸弹 / 单文件 > 50MB | 跳过 | `batch_import_file.status='failed', error_message='oversize'`,不投 parsing;100% failed → `partial_failed` |
| F7 | 包内含未识别扩展名 | 跳过 | 不写入 file 表,日志 INFO |
| F8 | DOCX 文件入流(worker 抛) | 单 task 失败 | catch → task `status='failed', error_message='docx_not_supported'`,failed++;DOCX 从批量上传白名单**移除** |
| F9 | PaddleOCR-VL 不可用(5xx/timeout) | 单 task 失败 | 重试 ≤ 3 次指数退避(延迟队列),仍失败 → DLQ,file `failed` |
| F10 | PaddleOCR-VL 持续不可用 | 大批失败 | 不做熔断;自然失败到 DLQ;`GET /batches/{id}` 可见 failed 比例;运维介入后 `POST /retry` |
| F11 | MedGo 不可用或超时 | 单 task 失败 | 同 F9 重试;成功后不重复 interp 已生成产物(幂等) |
| F12 | MedGo 显存压力告警 | 排队不 OOM | `medgo_sem` 收口外部并发 ≤ N;vLLM `--max-num-seqs` 双保险 |
| F13 | RabbitMQ 重连断开 | 在途消息 | pika BlockingConnection 自带重连循环;publish 失败时指数退避 3 次,仍失败 batch `partial_failed` |
| F14 | Worker 崩溃,半成品 batch | 一致性失联 | BatchSweeper 每 5min 巡检推进/续跑 |
| F15 | interp 重复执行(同 report_id en queue 两次) | 重复 MedGo 调用 | worker 起手查 `report_interpretation.status in ('running','completed')` 已存在则 ack 跳过 |
| F16 | bulk consumer 在非时段窗口启动 | bulk 队列堆积 | `nack(requeue=True) + sleep(5)`,不进 DLQ,日志可见堆积 |
| F17 | 管理员 cancel 批 | 已 publish task 继续跑 | `status='cancelled'`;extract worker 起手看到 cancelled → ack 提前返回;已 publish parsing/interp 任务仍完成(不撤回) |
| F18 | 同 batch 重复 publish `extract.task` | 重解压 | extract worker 起手查已有 file 行数,只补差 |
| F19 | DLQ 死信堆积 | 占盘 | `dead.letter` 设 `x-message-ttl=7d` 自动 drop;`GET /dead` 只读 |
| F20 | int 字段溢出 | 写库异常 | 全部用 `BigInteger`(SQLAlchemy `BigInteger` 映射 MySQL `BIGINT`) |

### 5.1 失败的统一观测

每个影响 batch 进度的失败(F5/F6/F8/F9/F11/F17)必须:
1. 写 `batch_import_file.status='failed', error_message=...`
2. 原子 `UPDATE batch_import SET failed=failed+1, updated_at=NOW() WHERE id=?`
3. 写日志 warning
4. **不**写进程级 DLQ 累计(batch file failed 与 MQ DLQ 是两个层级)

### 5.2 DLQ 与 file_failed 语义区分

- **`batch_import_file.status='failed'`** = 解析层重试穷尽,该文件死亡。`POST /retry` 可重生。
- **`dead.letter` 队列** = MQ 层重试穷尽或显式 `nack(requeue=False)`。通过消息体 `batch_id` header 关联 batch。`GET /batches/{id}/dead` 实现 `RabbitMQClient.consume_dead(batch_id)` 用 `basic_get` 拉取不消费。

### 5.3 熔断/降级 (YAGNI)

**第一版不实现熔断**。SLA 小时级、人工介入足够;加自动化反而引入延迟与隐性 bug。只暴露失败比例给前端。

### 5.4 关键不变式(代码评审与测试必须覆盖)

- I1. `batch_import.parsed_ok + interp_ok + failed <= total`(任一时刻)
- I2. 任一 `batch_import_file.status` 转 `parsed` 时,`parsed_ok++` 只一次(`UPDATE...WHERE status NOT IN ('parsed','interp_ok')`)
- I3. `status='completed'` 进入: `total>0 AND parsed_ok+interp_ok+failed==total AND failed==0`
- I4. `status='partial_failed'` 进入: `parsed_ok+interp_ok+failed==total AND failed>0`
- I5. extract worker publish 次数 == 新增 `batch_import_file` 行数(防双投)
- I6. `medgo_sem` 内部 active coroutine 数 ∈ {0..N},无泄漏

## 6. 改动清单 / 配置 / 迁移

### 6.1 文件改动

| 文件 | 类 | 改动要点 |
|------|----|----------|
| `app/modules/report/batch_router.py` | N | C1 8 个 endpoint(batches 创建/分片/完成/列表/详情/死信/重试/取消),admin role 校验,流式分片 5MB/片 |
| `app/modules/report/batch_service.py` | N | C2 状态机推进,幂等去重 |
| `app/modules/report/batch_models.py` | N | C4 ORM + DDL 同步到 start.sh |
| `app/modules/report/extract_worker.py` | N | C3 解压+过滤+幂等+publish parsing.bulk,zip 炸弹防护 |
| `app/core/retry.py` | N | C9 BACKOFFS + 延迟队列声明 |
| `app/core/batch_sweeper.py` | N | C11 巡检协程 5min 周期 |
| `app/ai/llm.py` | M | C5 medgo_sem 单例 + `_guarded(coro)` helper |
| `app/modules/report/service.py` | M | `_parse_text_with_llm` 包 sem;`130-141` 改延迟重投;`create_task` 支持 `priority='bulk'` + `batch_id`;写 `BatchImportFile.report_task_id` |
| `app/modules/report/worker.py` | M | C10 per-queue consume;bulk 时段过滤 |
| `app/modules/interpretation/worker.py` | M | C10 同上 + 删 `time.sleep` 退避;`running` 状态也跳过 |
| `app/modules/interpretation/interp_graph.py` | M | MedGo 调用 ~3 处 `async with medgo_sem:` |
| `app/modules/interpretation/judge_graph.py` | M | 同上 ~1 处 |
| `app/modules/chat/chat_graph.py` | M | 同上 ~2 处,per-session lock 保留 |
| `app/modules/chat/chat_planner.py` | M | 同上 ~1 处 |
| `app/modules/user_profile/service.py` | M | 同上 ~2 处(ai-summary) |
| `app/core/rabbitmq.py` | M | C8 新增 4 队列 + DLX + DLQ TTL;`consume_dead` 方法 |
| `app/modules/report/router.py` | M | F8 DOCX 从白名单移除;size 校验改流式(读前查 Content-Length,边读边累加) |
| `app/main.py` | M | 注册 `batch_router`;启 BatchSweeper asyncio task |
| `app/config.py` | M | 新增配置项 §6.2 |
| `.env`/`.env.example` | M | 同上 |
| `start.sh` | M | DDL 新增两表;启动 extract_worker 进程段(`259-280` 区域) |
| `infra/docker-compose.yml` | M | RabbitMQ volume 挂载 queue-reset 脚本(可选) |
| `infra/rabbitmq-queue-reset.sh` | N | 迁移用删旧队列脚本 |
| `tests/...` | N | §7 |

### 6.2 新增配置项 (env)

| Key | 默认 | 用途 |
|-----|------|------|
| `MEDGO_MAX_CONCURRENCY` | `2` | medgo_sem 上限 |
| `BATCH_ARCHIVE_MAX_SIZE` | `10737418240` (10GB) | 单包总大小上限 |
| `BATCH_CHUNK_SIZE` | `5242880` (5MB) | 分片大小 |
| `BATCH_CHUNK_TIMEOUT` | `7200` (s) | reaper 孤儿 uploading 阈值 |
| `BATCH_SWEEP_INTERVAL` | `300` (s) | BatchSweeper 周期 |
| `BATCH_SWEEP_STALL_THRESHOLD` | `1800` (s) | 未刷新判卡住 |
| `BULK_WINDOW_START` | `22` | bulk 时段起(24h) |
| `BULK_WINDOW_END` | `8` | bulk 时段止 |
| `BATCH_FILE_MAX_SIZE` | `52428800` (50MB) | 单条报告文件上限 |
| `DEAD_LETTER_TTL` | `604800` (7d) | DLQ 消息 TTL |

全带合理默认,不填也能跑。

### 6.3 部署迁移(冷启动一次)

唯一破坏性改动: RabbitMQ 队列声明加 `x-dead-letter-exchange` args,旧队列 args 不一致 → `PRECONDITION_FAILED`,必须先删旧队列再起。

```bash
# 1. 停服
bash stop.sh
# 2. 删 RabbitMQ 旧队列(docker 内)
docker exec hospital-rabbitmq rabbitmqctl delete_queue parsing.urgent || true
docker exec hospital-rabbitmq rabbitmqctl delete_queue parsing.normal || true
docker exec hospital-rabbitmq rabbitmqctl delete_queue interpretation.urgent || true
docker exec hospital-rabbitmq rabbitmqctl delete_queue interpretation.normal || true
docker exec hospital-rabbitmq rabbitmqctl delete_queue dead.letter || true
# 3. 启动(start.sh 里 rabbitmq.py 第一次 queue_declare 创建带 DLX args 的新队列)
bash start.sh
```

DB 迁移无破坏,新表 `CREATE TABLE IF NOT EXISTS`。

**回滚**: 单向迁移。回滚必须同时回滚代码 + 重删队列重建。
`infra/rabbitmq-queue-reset.sh --revert`:删带 DLX 的新队列,旧代码再声明无 args 重建。生产上前先在 dev 跑通一次。

### 6.4 vLLM 启动参数加固 (可选)

`start.sh:155-159` MedGo vLLM 启动命令加 `--max-num-seqs 4`:

```
vllm serve /data/models/MedGo --port 8004 --tensor-parallel-size 4 \
  --max-model-len 32768 --gpu-memory-utilization 0.6 --disable-custom-all-reduce \
  --enforce-eager --enable-auto-tool-choice --tool-call-parser hermes \
  --max-num-seqs 4
```

**应用层 sem=2 + vLLM max-num-seqs=4** 双保险:即使应用 sem 失效,vLLM 也绝不可能瞬间涌入 256 并发打爆 KV cache。需重启 MedGo vLLM(~40s 停),第一版上线时一起做。是否做由用户决定。

## 7. 测试策略

### 7.1 分层与优先级

| 层 | 范围 | 框架 | 优先级 |
|----|------|------|--------|
| 单元 | C2 BatchService 状态机 / 幂等 / sweeper / retry backoff | pytest + SQLite `:memory:` | 必做 |
| 单元 | C3 zip 炸弹防护 / 过滤 / 幂等对账 | pytest + tmpdir | 必做 |
| 单元 | C7 medgo_sem 收口(并发计数) | pytest + asyncio | 必做 |
| 单元 | C9 backoff 表 / C10 时段过滤跨午夜 | pytest 参数化 | 必做 |
| 集成 | C1+C2 端到端 创 batch → 喂分片 → complete → 查进度 | pytest + httpx AsyncClient + SQLite | 必做 |
| 集成 | DLQ 正确接入(队列 args → 失败消息进 dead.letter) | pytest + testcontainers-rabbitmq | 应做 |
| 集成 | extract → parsing → interp bulk 时段过滤链路 | 同上,monkeypatch MedGo mock | 应做 |
| 压力 | 1000 份小 PDF 跑透 timing / 显存曲线 | bash 脚本 + nvidia-smi 5s 采样 | 上线前手做一次 |

### 7.2 必做测试用例

#### T1 — BatchService 状态机 `tests/test_batch_service.py`

- T1.1 create_batch → uploading;append 多次;finalize → extracting
- T1.2 幂等:同 (batch_id,crc32) 二次 insert → 同一 file_id,total 不变
- T1.3 increment_progress(parsed_ok) 二次同 file_id → 只 +1
- T1.4 retry_failed 后 failed→queued;三次后停
- T1.5 全 parsed → completed;一项 failed → partial_failed
- T1.6 已 completed 不允许 cancel;uploading 允许
- T1.7 sweeper 推进 5 类卡住场景

#### T2 — extract_worker `tests/test_extract_worker.py`

- T2.1 zip(3 pdf) → 3 file 行 + 3 publish
- T2.2 zip 炸弹(单 1GB) → skip + failed='oversize'
- T2.3 混合扩展名:pdf/jpg/png 通过,doc/docx/.txt 跳过
- T2.4 同 crc32 两文件 → 1 file,不重复 publish
- T2.5 batch='cancelled' → 不 publish 且 ack
- T2.6 重投 extract.task → 只补差
- T2.7 损坏 zip → partial_failed

#### T3 — medgo_sem `tests/test_medgo_sem.py`

- T3.1 N+3 并发 ainvoke → active ≤ N
- T3.2 sync(asyncio.run) + async 混发 → 总并发 ≤ N
- T3.3 acquire 中 task cancel → release 正确

#### T4 — retry & timewindow `tests/test_retry_timewindow.py`

- T4.1 backoff_for_retry(0/1/2/9) = 10/60/600/600
- T4.2 is_bulk_window_now 跨午夜正确
- T4.3 延迟队列 TTL 过期后路由回原队列(testcontainers)

#### T5 — DLQ `tests/test_dlq.py`

- T5.1 故意抛异常 → 消息走 DLX → dead.letter
- T5.2 dead.letter 7d 后自动 drop(TTL)
- T5.3 GET /batches/{id}/dead 拉到对应死信

#### T6 — 端到端上传 `tests/test_batch_upload_e2e.py`

- T6.1 创 batch + 3 分片 + complete → extract → 3 parsing 任务入队
- T6.2 进度查询 total/parsed_ok/failed 正确
- T6.3 乱序分片 → complete 拼装 CRC 通过
- T6.4 CRC 不匹配 → 400 + cancelled
- T6.5 单包超 10GB → 400

### 7.3 Mock 策略

- **MedGo**: monkeypatch `ChatOpenAI.ainvoke` 返回固定延迟 + 16K token 填充,测收口行为
- **PaddleOCR-VL**: mock httpx `Client.post` 写死 markdown
- **RabbitMQ**: testcontainers `rabbitmq:3.12-management`,标记 `@pytest.mark.integration`
- **DB**: SQLite `:memory:` + `Base.metadata.create_all`
- **时钟**: freezegun 冻 `datetime.now()` 测 bulk window / sweeper

### 7.4 关键不变式检验

§5.4 的 I1–I6 写成显式 assertion 入 sweeper 巡检与单测。

### 7.5 CI / 本地运行

新增 dev deps:`pytest`, `pytest-asyncio`, `httpx`(已有), `testcontainers`, `freezegun`。

```bash
cd backend && .venv/bin/pytest tests/ -m "not integration" -q
cd backend && .venv/bin/pytest tests/ -m integration -q
```

### 7.6 上线前手动压测脚本 `scripts/bench-batch.sh`

1. 生成 1000 个小 PDF(1-page ~200 字符)
2. zip 打包
3. curl 流式上传触发 batch
4. 轮询 GET /batches/{id} 直到完成
5. 每 5s 采样 nvidia-smi 显存 + MedGo 队列深度
6. 输出 `<max_gpu_mem_GB> <wall_clock_min> <fail_count>`

**验收门槛**:
- 1000 份单字节 PDF 60min 内跑完(小时级 SLA)
- 期间 chat 路径仍可正常对话
- 任一卡显存峰值 < 40GB(MedGo util 0.6 已占 27.8GB,余 ~17GB)

## 8. 未决事项 / FUTURE 钩子

- **跨批 crc32 去重复用**: 在 `BatchService.handle_extracted_file` 标 `# FUTURE: query globally by crc32 + file_size, reuse old report_task_id` 不实现
- **多 worker 横扩**: 第一版每服务 1 worker,未来按 batch 规模弹性扩
- **真熔断**: 第一版不实现,人工介入
- **OPCAT 监控**: 第一版不加 Prometheus exporter,可观性靠日志与 GET /batches