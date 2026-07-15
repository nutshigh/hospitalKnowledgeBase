# 批量上传体检报告解析 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增管理员批量上传 zip/tar 包并自动解析上千份体检报告的能力,同时在现有 RabbitMQ + vLLM 架构上做最小但有根因性的改造(真 DLQ、指数退避、MedGo 全局并发闸、bulk 时段限流)。

**Architecture:** 复用现有 pika 阻塞 worker。新增 `batch_import`/`batch_import_file` 两表 + 一个轻量 `extract_worker`(只解压不发 GPU)。所有 MedGo 调用点统一通过 `asyncio.Semaphore(N)` 收口,根除 chat 路径绕过 MQ 导致的显存爆炸风险。RabbitMQ 增加 `parsing.bulk`/`interpretation.bulk` + 6 个 per-source retry 队列 + 真 DLX。bulk consumer 仅在 `BULK_WINDOW` 时段消费。

**Tech Stack:** FastAPI / SQLAlchemy / pika (RabbitMQ) / LangChain ChatOpenAI (MedGo via vLLM) / pytest

**Spec:** `docs/superpowers/specs/2026-07-15-batch-report-upload-design.md`(下文简称 "Spec")。本计划与 Spec 一致;若冲突以 Spec 为准。

## Global Constraints

- Python 3.10,后端主 venv 为 `backend/.venv`(cu12)。**禁止往 `backend/pyproject.toml` 加 vllm 依赖**(见 AGENTS.md)。
- 现有 worker 单进程 `prefetch=1`;第一版不增加 worker 进程数。
- 不动 vLLM venv / GPU 分配 / start.sh 的 vLLM 启动参数(可选加固 `--max-num-seqs 4` 由用户决定,本计划不强制)。
- 所有新增 env 必须有合理默认,不填也能跑。
- RabbitMQ 队列改 args 是单向迁移,必须先删旧队列再起服务(Spec §6.3)。
- **TDD**:每个新组件先写失败测试,再写最小实现。
- 测试 DB 用 SQLite `:memory:` + `Base.metadata.create_all`;不依赖 MySQL。
- 所有 `Integer` 计数/size 字段用 `BigInteger`(MySQL `BIGINT`,Spec F20)。
- 测试命令:`cd backend && .venv/bin/pytest tests/path -v`

---

## File Structure

新文件(N)、改文件(M):

| 文件 | 类 | 责任 |
|------|----|------|
| `app/modules/report/batch_models.py` | N | `BatchImport` / `BatchImportFile` ORM |
| `app/modules/report/batch_service.py` | N | 状态机 + 幂等协调 |
| `app/modules/report/batch_router.py` | N | 8 个 HTTP endpoint |
| `app/modules/report/extract_worker.py` | N | 解压 + 幂等 + publish parsing.bulk |
| `app/core/batch_sweeper.py` | N | 后台巡检协程 |
| `app/core/retry.py` | N | backoff 表 + `is_bulk_window_now` |
| `app/ai/llm.py` | M | `medgo_sem` + `_guarded` helper |
| `app/core/rabbitmq.py` | M | 6 主队列 + 6 retry + DLX + `consume_dead` |
| `app/modules/report/service.py` | M | `create_task` 支持 `priority='bulk'`+`batch_id`;删立即重投;`_parse_text_with_llm` 包 sem |
| `app/modules/report/worker.py` | M | per-queue consume + bulk 时段 + retry 队列消费 |
| `app/modules/interpretation/worker.py` | M | per-queue + bulk + 删 sleep + running 跳过 |
| `app/modules/interpretation/{interp_graph,judge_graph}.py` | M | MedGo 调用包 sem |
| `app/modules/chat/{chat_graph,chat_planner}.py` | M | 同上 |
| `app/modules/user_profile/service.py` | M | 同上 |
| `app/modules/report/router.py` | M | DOCX 移除 + 流式 size 校验 |
| `app/main.py` | M | 注册 batch_router + 启动 sweeper |
| `app/config.py`, `.env` | M | 新增 env |
| `start.sh` | M | DDL 两表 + 启动 extract_worker |
| `infra/rabbitmq-queue-reset.sh` | N | 迁移用删队列脚本 |
| `tests/test_batch_service.py` 等多个 | N | 见各任务 |
| `scripts/bench-batch.sh` | N | 上线前压测脚本 |

依赖顺序:T1(配置/依赖基础) → T2(模型) → T3(medgo_sem) → T4(wrap 调用点) → T5(rabbitmq) → T6(retry) → T7(batch_service) → T8(extract_worker) → T9(report service/worker) → T10(interp worker) → T11(batch_router) → T12(sweeper) → T13(main/router 收尾) → T14(report router DOCX/流式) → T15(迁移脚本+start.sh+压测)

---

## Task 1: 测试基础设施 + 配置项

**Files:**
- Modify: `backend/pyproject.toml`(dev deps 记入 `[tool.uv]` dev 区或约束 pyproject 不强制装;`pytest`/`pytest-asyncio` 已在主 deps,只需补 `freezegun`)
- Modify: `backend/app/config.py:4-109`(新增 env 字段)
- Modify: `backend/.env`(末尾追加,可选)
- Test: `backend/tests/test_config_batch.py`(N)

**Interfaces:**
- Produces: `settings.MEDGO_MAX_CONCURRENCY`, `settings.BATCH_ARCHIVE_MAX_SIZE`, `settings.BATCH_CHUNK_SIZE`, `settings.BATCH_CHUNK_TIMEOUT`, `settings.BATCH_SWEEP_INTERVAL`, `settings.BATCH_SWEEP_STALL_THRESHOLD`, `settings.BULK_WINDOW_START`, `settings.BULK_WINDOW_END`, `settings.BATCH_FILE_MAX_SIZE`, `settings.DEAD_LETTER_TTL`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config_batch.py
import os
def test_batch_config_defaults(monkeypatch):
    for k in ["MEDGO_MAX_CONCURRENCY","BATCH_ARCHIVE_MAX_SIZE","BATCH_CHUNK_SIZE",
              "BATCH_SWEEP_INTERVAL","BULK_WINDOW_START","BULK_WINDOW_END",
              "BATCH_FILE_MAX_SIZE","DEAD_LETTER_TTL"]:
        monkeypatch.delenv(k, raising=False)
    from app.config import Settings
    s = Settings()
    assert s.MEDGO_MAX_CONCURRENCY == 2
    assert s.BATCH_ARCHIVE_MAX_SIZE == 10737418240
    assert s.BATCH_CHUNK_SIZE == 5242880
    assert s.BATCH_SWEEP_INTERVAL == 300
    assert s.BULK_WINDOW_START == 22
    assert s.BULK_WINDOW_END == 8
    assert s.BATCH_FILE_MAX_SIZE == 52428800
    assert s.DEAD_LETTER_TTL == 604800
```

- [ ] **Step 2: Run test → FAIL**

```bash
cd backend && .venv/bin/pytest tests/test_config_batch.py -v
# Expected: FAIL AttributeError: 'Settings' object has no attribute 'MEDGO_MAX_CONCURRENCY'
```

- [ ] **Step 3: Add config fields**

在 `app/config.py` 的 `File Storage` 字段之前(约 106 行)插入批次配置块:

```python
    # Batch Import (spec §6.2)
    MEDGO_MAX_CONCURRENCY: int = 2
    BATCH_ARCHIVE_MAX_SIZE: int = 10737418240  # 10GB
    BATCH_CHUNK_SIZE: int = 5242880            # 5MB
    BATCH_CHUNK_TIMEOUT: int = 7200            # 2h,孤儿 uploading 阈值
    BATCH_SWEEP_INTERVAL: int = 300            # 5min
    BATCH_SWEEP_STALL_THRESHOLD: int = 1800    # 30min
    BULK_WINDOW_START: int = 22
    BULK_WINDOW_END: int = 8
    BATCH_FILE_MAX_SIZE: int = 52428800        # 50MB
    DEAD_LETTER_TTL: int = 604800              # 7d
```

- [ ] **Step 4: Add `freezegun` dev dep**

运行:`cd backend && .venv/bin/pip install freezegun testcontainers` (或加到 pyproject dev group)。**注意**:本步不强求 testcontainers;集成测试标记为可选。

- [ ] **Step 5: Run test → PASS**

```bash
cd backend && .venv/bin/pytest tests/test_config_batch.py -v
# Expected: PASS
```

- [ ] **Step 6: Commit**

```bash
git add app/config.py tests/test_config_batch.py
git commit -m "feat(batch): 新增批量导入相关配置项"
```

---

## Task 2: batch_import 数据模型 + DDL

**Files:**
- Create: `app/modules/report/batch_models.py`
- Modify: `start.sh:99-134`(DDL 块尾追加两表)
- Test: `tests/test_batch_models.py`

**Interfaces:**
- Produces: `BatchImport`, `BatchImportFile` ORM 类,字段与 Spec §4.1 完全一致;两张表通过 `Base.metadata.create_all` 在测试 DB 中可建表。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_batch_models.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.base import Base
from app.modules.report.batch_models import BatchImport, BatchImportFile


def test_create_tables_in_memory_sqlite():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    b = BatchImport(
        id="b1", hospital_id="H001", user_id="admin",
        filename="x.zip", archive_path="/tmp/x.zip",
    )
    db.add(b); db.commit()

    f = BatchImportFile(
        id="f1", batch_id="b1", file_path="u/x.pdf",
        file_size=1024, crc32="deadbeef",
    )
    db.add(f); db.commit()

    assert db.query(BatchImport).count() == 1
    assert db.query(BatchImportFile).count() == 1
    assert b.status == "uploading"
    assert b.total == 0 and b.parsed_ok == 0 and b.interp_ok == 0 and b.failed == 0
    assert f.status == "queued"
    # unique constraint(batch_id, crc32)
    dup = BatchImportFile(id="f2", batch_id="b1", file_path="u/x2.pdf",
                          file_size=1, crc32="deadbeef")
    db.add(dup)
    try:
        db.commit()
        assert False, "should raise on duplicate (batch_id,crc32)"
    except Exception:
        db.rollback()
```

- [ ] **Step 2: Run → FAIL**

```bash
cd backend && .venv/bin/pytest tests/test_batch_models.py -v
# Expected: FAIL ImportError: cannot import 'batch_models'
```

- [ ] **Step 3: Create batch_models.py**

```python
# backend/app/modules/report/batch_models.py
from sqlalchemy import Column, String, BigInteger, Text, DateTime, ForeignKey, UniqueConstraint, func
from app.models.base import Base


class BatchImport(Base):
    __tablename__ = "batch_import"

    id = Column(String(36), primary_key=True)            # uuid4 hex
    hospital_id = Column(String(32), nullable=False)
    user_id = Column(String(64), nullable=False)
    filename = Column(String(255), nullable=False)
    archive_path = Column(String(512), nullable=False)
    total = Column(BigInteger, default=0)
    parsed_ok = Column(BigInteger, default=0)
    interp_ok = Column(BigInteger, default=0)
    failed = Column(BigInteger, default=0)
    status = Column(String(24), default="uploading", nullable=False)
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    completed_at = Column(DateTime)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class BatchImportFile(Base):
    __tablename__ = "batch_import_file"

    id = Column(String(36), primary_key=True)
    batch_id = Column(String(36), ForeignKey("batch_import.id"), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_size = Column(BigInteger, default=0)
    crc32 = Column(String(8), nullable=False, index=True)
    status = Column(String(24), default="queued", nullable=False)
    report_task_id = Column(BigInteger)
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("batch_id", "crc32", name="uq_batch_file"),)
```

- [ ] **Step 4: Run → PASS**

```bash
cd backend && .venv/bin/pytest tests/test_batch_models.py -v
```

- [ ] **Step 5: Add DDL to start.sh**

在 `start.sh:99-134` 的建表块末尾(其它 `CREATE TABLE IF NOT EXISTS` 之后)追加:

```bash
mysql -h$MYSQL_HOST -u$MYSQL_USER -p$MYSQL_PASSWORD $DB_NAME <<EOF
CREATE TABLE IF NOT EXISTS batch_import (
  id VARCHAR(36) PRIMARY KEY,
  hospital_id VARCHAR(32) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  filename VARCHAR(255) NOT NULL,
  archive_path VARCHAR(512) NOT NULL,
  total BIGINT NOT NULL DEFAULT 0,
  parsed_ok BIGINT NOT NULL DEFAULT 0,
  interp_ok BIGINT NOT NULL DEFAULT 0,
  failed BIGINT NOT NULL DEFAULT 0,
  status VARCHAR(24) NOT NULL DEFAULT 'uploading',
  error_message TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_batch_status (status),
  KEY idx_batch_hospital (hospital_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS batch_import_file (
  id VARCHAR(36) PRIMARY KEY,
  batch_id VARCHAR(36) NOT NULL,
  file_path VARCHAR(512) NOT NULL,
  file_size BIGINT NOT NULL DEFAULT 0,
  crc32 VARCHAR(8) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'queued',
  report_task_id BIGINT,
  error_message TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_batch_file (batch_id, crc32),
  KEY idx_bfile_status (status),
  CONSTRAINT fk_bfile_batch FOREIGN KEY (batch_id) REFERENCES batch_import(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
EOF
```

- [ ] **Step 6: Commit**

```bash
git add app/modules/report/batch_models.py start.sh tests/test_batch_models.py
git commit -m "feat(batch): batch_import/batch_import_file 模型与 DDL"
```

---

## Task 3: medgo_sem 收口闸门

**Files:**
- Modify: `app/ai/llm.py`(新增 sem 与 helper)
- Test: `tests/test_medgo_sem.py`

**Interfaces:**
- Produces: `app.ai.llm.medgo_sem`(`asyncio.Semaphore` 单例),`app.ai.llm._guarded(coro)` async helper。供 Task 4 wrap 调用点使用。
- Consumes: `settings.MEDGO_MAX_CONCURRENCY`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_medgo_sem.py
import asyncio
import pytest

@pytest.mark.asyncio
async def test_concurrency_capped_at_n(monkeypatch):
    monkeypatch.setenv("MEDGO_MAX_CONCURRENCY", "2")
    # 重新加载以让 semaphore 用新值
    import importlib
    import app.ai.llm as llm_mod
    importlib.reload(llm_mod)
    from app.ai.llm import medgo_sem, _guarded

    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def task():
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1

    await asyncio.gather(*[_guarded(task()) for _ in range(5)])
    assert peak <= 2, f"peak exceeded N: {peak}"
    assert peak == 2  # 确实用满了


@pytest.mark.asyncio
async def test_release_on_cancel(monkeypatch):
    import importlib
    monkeypatch.setenv("MEDGO_MAX_CONCURRENCY", "1")
    import app.ai.llm as llm_mod
    importlib.reload(llm_mod)
    from app.ai.llm import medgo_sem, _guarded

    async def slow():
        await asyncio.sleep(10)

    t = asyncio.create_task(_guarded(slow()))
    await asyncio.sleep(0.05)
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    # 关键:sem 必须已释放,新协程可立即获取
    async def quick():
        return 42
    r = await asyncio.wait_for(_guarded(quick()), timeout=1.0)
    assert r == 42
```

- [ ] **Step 2: Run → FAIL**

```bash
cd backend && .venv/bin/pytest tests/test_medgo_sem.py -v
# Expected: FAIL ImportError: cannot import 'medgo_sem'
```

- [ ] **Step 3: Add medgo_sem to llm.py**

在 `app/ai/llm.py` 顶部 import 区后插入:

```python
import asyncio
import os

_MEDGO_MAX = int(os.getenv("MEDGO_MAX_CONCURRENCY", "2"))
medgo_sem = asyncio.Semaphore(_MEDGO_MAX)


async def _guarded(coro):
    """统一 MedGo 并发计数闸。所有 MedGo 调用必须经此包装。"""
    async with medgo_sem:
        return await coro
```

注意:模块级 semaphore 在模块首次 import 时构造;测试用 `importlib.reload` 重建以读 env。生产环境 MedGo 进程启动时 env 已就位,不受影响。

- [ ] **Step 4: Run → PASS**

```bash
cd backend && .venv/bin/pytest tests/test_medgo_sem.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/ai/llm.py tests/test_medgo_sem.py
git commit -m "feat(llm): medgo 全局 asyncio.Semaphore 收口闸"
```

---

## Task 4: wrap MedGo 调用点入 medgo_sem

**Files:**
- Modify: `app/modules/report/service.py`(`_parse_text_with_llm`,sync worker 路径)
- Modify: `app/modules/interpretation/interp_graph.py`(~3 处)
- Modify: `app/modules/interpretation/judge_graph.py`(~1 处)
- Modify: `app/modules/chat/chat_graph.py`(~2 处)
- Modify: `app/modules/chat/chat_planner.py`(~1 处)
- Modify: `app/modules/user_profile/service.py`(~2 处)
- Test: `tests/test_medgo_wrap.py`

**约定**:async 调用点用 `async with medgo_sem:` 包裹;sync worker 路径(`_parse_text_with_llm`)用 `asyncio.run(_guarded(...))` 桥接。

**Interfaces:**
- Consumes: `app.ai.llm.medgo_sem`,`app.ai.llm._guarded`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_medgo_wrap.py
"""验证所有 MedGo 调用点都被 medgo_sem 收口。
通过 monkeypatch ChatOpenAI.{invoke,ainvoke,astream} 计数,统计 acquire 期间的活动。
此测试不启动真 vLLM;只验证 wrap 是否存在。"""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_report_parse_text_uses_sem(monkeypatch):
    """report/service._parse_text_with_llm 调用必须经过 sem。"""
    import app.ai.llm as llm_mod
    calls = {"acquired": 0}

    orig_sem = llm_mod.medgo_sem
    # 用一个会追踪的 sem
    class TrackingSem:
        async def __aenter__(self):
            calls["acquired"] += 1
            return self
        async def __aexit__(self, *a):
            pass
    llm_mod.medgo_sem = TrackingSem()
    try:
        # mock ChatOpenAI.invoke
        with patch("app.ai.llm.ChatOpenAI") as M:
            M.return_value.invoke.return_value = type("R", (), {"content": '{"name":null,"gender":null,"age":null,"report_date":null,"indicators":[]}'})()
            from app.modules.report.service import _parse_text_with_llm
            _parse_text_with_llm("some text")
        assert calls["acquired"] >= 1
    finally:
        llm_mod.medgo_sem = orig_sem
```

- [ ] **Step 2: Run → FAIL**

```bash
cd backend && .venv/bin/pytest tests/test_medgo_wrap.py -v
# Expected: FAIL acquired == 0 (调用未经过 sem)
```

- [ ] **Step 3: Wrap report/service.py:_parse_text_with_llm**

把 `service.py:195-196` 的 `model.invoke(...)` 改为走 sem。由于这是 sync worker 路径,用 `asyncio.run(_guarded(...async...))`。但 `model.invoke` 是 sync API,需要先转 async。最简方案:

将 `_parse_text_with_llm` 改为 async,内部用 `await model.ainvoke(...)`,然后用 `asyncio.run(_guarded(_async_parse(...)))` 顶层入口 sync 包装。

具体实现:在 `service.py` 顶部加 `import asyncio`,`from app.ai.llm import medgo_sem`。改写 `_parse_text_with_llm`:

```python
async def _parse_text_with_llm_async(text: str) -> dict:
    """实际 async 解析,包裹在 medgo_sem 内。"""
    from app.ai.llm import get_chat_model, _guarded
    prompt = _build_parse_prompt(text)
    model = get_chat_model()

    async def _call():
        return await model.ainvoke([("user", prompt)], max_tokens=16384)

    resp = (await _guarded(_call())).content
    return _parse_llm_json(resp)


def _parse_text_with_llm(text: str) -> dict:
    """sync 入口,供 worker 调用;内部通过 asyncio.run 进入 async + medgo_sem。"""
    return asyncio.run(_parse_text_with_llm_async(text))


def _build_parse_prompt(text: str) -> str:
    return f"""从以下体检报告文本中提取信息，返回 JSON 格式（不要 Markdown 代码块）：

{{
  "name": "姓名",
  "gender": "男或女",
  "age": 年龄数字或null,
  "report_date": "YYYY-MM-DD或null",
  "indicators": [
    {{"item_name": "指标名称", "result": "检测结果", "unit": "单位", "ref_low": "参考下限", "ref_high": "参考上限"}}
  ]
}}

规则：
1. 姓名从"尊敬的XXX先生/女士"或"姓名:XXX"提取
2. 性别："先生"→男，"女士"→女
3. 年龄：从"XX岁"提取数字
4. 参考范围如"3.5-9.5"→ref_low="3.5", ref_high="9.5"；如"<5.0"→ref_low="", ref_high="5.0"
5. 只提取化验指标数据（血常规、生化、免疫等），不提取问卷、个人信息
6. 没有的字段填 null

体检报告文本：
{text[:24000]}
"""


def _parse_llm_json(resp: str) -> dict:
    import json, re
    from json_repair import repair_json
    match = re.search(r'\{[\s\S]*\}', resp)
    if not match:
        raise ValueError(f"LLM did not return valid JSON: {resp[:200]}")
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        data = json.loads(repair_json(match.group()))
    for ind in data.get("indicators", []):
        ref = ind.pop("ref_range", None)
        if ref and "ref_low" not in ind:
            from app.core.vlm_client import _parse_ref_range
            lo, hi = _parse_ref_range(str(ref))
            ind["ref_low"] = lo
            ind["ref_high"] = hi
    return data
```

- [ ] **Step 4: Wrap async 调用点(interp/judge/chat/planner/user_profile)**

对每个 `model.ainvoke(...)` / `model.astream(...)` 调用点,改成 `async with medgo_sem:` 包裹。例:

```python
# interp_graph.py 原代码
result = await model.ainvoke(...)
# 改为
from app.ai.llm import medgo_sem
async with medgo_sem:
    result = await model.ainvoke(...)
```

对每个文件,在顶部 import 区加 `from app.ai.llm import medgo_sem`,然后给每个 `await model.a*` 调用加 `async with medgo_sem:` 缩进包裹。

**精确位置**(用 grep 确认后改):
- `interp_graph.py`: 3 处 `model.ainVOKE`/agent 调用(参考 spec §6.1 探索报告标注的位置)
- `judge_graph.py`: 1 处
- `chat_graph.py`: 2 处(`astream` 调用,per-session lock 不动)
- `chat_planner.py`: 1 处
- `user_profile/service.py`: 2 处(`ainvoke` for ai-summary)

> 评审备注:sync worker 中的 `report/service._parse_text_with_llm` 走 `asyncio.run`,会为每次 MedGo 调用启动一个新事件循环。开销可接受(Spec §4.5)。不要尝试在 worker 起手就启动长期 loop 并复用 —— 与 pika BlockingConnection 的 thread model 冲突,易踩坑。

- [ ] **Step 5: Run all existing + new tests**

```bash
cd backend && .venv/bin/pytest tests/test_medgo_wrap.py tests/test_medgo_sem.py -v
```

- [ ] **Step 6: Commit**

```bash
git add app/modules/report/service.py app/modules/interpretation/interp_graph.py \
        app/modules/interpretation/judge_graph.py app/modules/chat/chat_graph.py \
        app/modules/chat/chat_planner.py app/modules/user_profile/service.py \
        tests/test_medgo_wrap.py
git commit -m "feat(llm): 所有 MedGo 调用点接入 medgo_sem 收口闸"
```

---

## Task 5: RabbitMQ 多队列 + DLX + retry 队列

**Files:**
- Modify: `app/core/rabbitmq.py`
- Test: `tests/test_rabbitmq_queues.py`(单测只验声明结构,不连真 RabbitMQ;集成测试在 Task 16)

**Interfaces:**
- Produces: `RabbitMQClient` 新增类属性 `QUEUES`(6 主队列)、`RETRY_QUEUES`(6 retry 队列,routing_key→target)、`DLX`、`consume_dead(batch_id)` 方法;`publish` 支持 routing key 含 `bulk`;新增 `publish_retry(routing_key, body, expiration_ms)` 助手。
- Consumes: `settings.DEAD_LETTER_TTL`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_rabbitmq_queues.py
"""验队列配置结构(不连真 RabbitMQ)。"""
from app.core.rabbitmq import RabbitMQClient

def test_queue_topology():
    c = RabbitMQClient
    assert set(c.QUEUES.values()) == {
        "parsing.urgent", "parsing.normal", "parsing.bulk",
        "interpretation.urgent", "interpretation.normal", "interpretation.bulk",
    }
    assert set(c.RETRY_QUEUES.keys()) == {
        "parsing.urgent.retry", "parsing.normal.retry", "parsing.bulk.retry",
        "interpretation.urgent.retry", "interpretation.normal.retry", "interpretation.bulk.retry",
    }
    # 每个 retry 队列 DLX 回对应的原队列
    assert c.RETRY_QUEUES["parsing.bulk.retry"] == "parsing.bulk"
    assert c.RETRY_QUEUES["interpretation.bulk.retry"] == "interpretation.bulk"
    assert c.DLX == "hospital.dlx"
    assert c.DEAD_LETTER_QUEUE == "dead.letter"


def test_routing_for_bulk_priority():
    """publish bulk 任务时 routing_key 应为 '<type>.bulk'."""
    from app.core.rabbitmq import TaskMessage
    # priority=2 -> bulk 扩展语义; 见 step 3 重新设计 priority 字段
    msg = TaskMessage(task_type="parsing", hospital_id="H", priority="bulk", payload={})
    rk = msg.routing_key()
    assert rk == "parsing.bulk"
    msg2 = TaskMessage(task_type="parsing", hospital_id="H", priority="urgent", payload={})
    assert msg2.routing_key() == "parsing.urgent"
    msg3 = TaskMessage(task_type="parsing", hospital_id="H", priority="normal", payload={})
    assert msg3.routing_key() == "parsing.normal"
```

- [ ] **Step 2: Run → FAIL**

```bash
cd backend && .venv/bin/pytest tests/test_rabbitmq_queues.py -v
```

- [ ] **Step 3: Rewrite rabbitmq.py**

将 `app/core/rabbitmq.py` 完整改写。关键点:
- `TaskMessage.priority` 从 int 改为 `str` ∈ `{"urgent","normal","bulk"}`(向后兼容:`int 1/0` → urgent/normal,见 routing_key 实现)
- `TaskMessage.routing_key()` 方法
- `QUEUES` 含 6 个,`RETRY_QUEUES` 6 个 DLX 回原队列
- `_ensure_resources` 声明 DLX + 6 主队列(带 `x-dead-letter-exchange`)+ DLQ(带 `x-message-ttl`)+ 6 retry 队列(带 `x-dead-letter-exchange` 指回主 exchange)
- `publish` body 加 `batch_id` header(从 payload 取,无则 None)用于死信归属
- `publish_retry(routing_key, body, expiration_ms)`:发到对应 `<rk>.retry` 队列
- `consume_dead(batch_id)`:用 `basic_get` 拉 dead.letter,过滤 `batch_id` header 匹配的,**不消费**

完整实现(替换 `RabbitMQClient` 类整体 + `TaskMessage`):

```python
from dataclasses import dataclass, field


@dataclass
class TaskMessage:
    task_type: str
    hospital_id: str
    priority: str = "normal"  # "urgent"|"normal"|"bulk"
    payload: dict = field(default_factory=dict)

    def routing_key(self) -> str:
        p = self.priority
        if isinstance(p, int):
            p = "urgent" if p else "normal"
        return f"{self.task_type}.{p}"


class RabbitMQClient:
    EXCHANGE = "hospital.tasks"
    DLX = "hospital.dlx"
    QUEUES = {
        "parsing.urgent": "parsing.urgent",
        "parsing.normal": "parsing.normal",
        "parsing.bulk": "parsing.bulk",
        "interpretation.urgent": "interpretation.urgent",
        "interpretation.normal": "interpretation.normal",
        "interpretation.bulk": "interpretation.bulk",
    }
    RETRY_QUEUES = {
        "parsing.urgent.retry": "parsing.urgent",
        "parsing.normal.retry": "parsing.normal",
        "parsing.bulk.retry": "parsing.bulk",
        "interpretation.urgent.retry": "interpretation.urgent",
        "interpretation.normal.retry": "interpretation.normal",
        "interpretation.bulk.retry": "interpretation.bulk",
    }
    DEAD_LETTER_QUEUE = "dead.letter"

    def __init__(self):
        self.connection = None
        self.channel = None

    def _connect(self):
        import pika
        from app.config import settings
        creds = pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD)
        params = pika.ConnectionParameters(
            host=settings.RABBITMQ_HOST, port=settings.RABBITMQ_PORT,
            credentials=creds, heartbeat=0,
        )
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()

    def _ensure_resources(self):
        from app.config import settings
        ch = self.channel
        ch.exchange_declare(exchange=self.EXCHANGE, exchange_type="topic", durable=True)
        ch.exchange_declare(exchange=self.DLX, exchange_type="topic", durable=True)
        main_args = {"x-dead-letter-exchange": self.DLX, "x-dead-letter-routing-key": "dead"}
        for q in self.QUEUES.values():
            ch.queue_declare(queue=q, durable=True, arguments=main_args)
            ch.queue_bind(exchange=self.EXCHANGE, queue=q, routing_key=q)
        for rq, target in self.RETRY_QUEUES.items():
            args = {"x-dead-letter-exchange": self.EXCHANGE, "x-dead-letter-routing-key": target}
            ch.queue_declare(queue=rq, durable=True, arguments=args)
            ch.queue_bind(exchange=self.EXCHANGE, queue=rq, routing_key=rq)
        ch.queue_declare(queue=self.DEAD_LETTER_QUEUE, durable=True,
                         arguments={"x-message-ttl": settings.DEAD_LETTER_TTL * 1000})
        ch.queue_bind(exchange=self.DLX, queue=self.DEAD_LETTER_QUEUE, routing_key="dead")

    def _ensure(self):
        if not self.connection or self.connection.is_closed:
            self._connect()
            self._ensure_resources()

    def publish(self, task: TaskMessage):
        self._ensure()
        batch_id = task.payload.get("batch_id")
        body_dict = {
            "task_type": task.task_type,
            "hospital_id": task.hospital_id,
            "payload": task.payload,
        }
        props = pika.BasicProperties(
            delivery_mode=2,
            headers={"batch_id": batch_id} if batch_id else {},
        )
        try:
            self.channel.basic_publish(
                exchange=self.EXCHANGE, routing_key=task.routing_key(),
                body=json.dumps(body_dict), properties=props,
            )
        except (pika.exceptions.ConnectionClosed, pika.exceptions.StreamLostError,
                pika.exceptions.ChannelClosed):
            self._ensure()
            self.channel.basic_publish(
                exchange=self.EXCHANGE, routing_key=task.routing_key(),
                body=json.dumps(body_dict), properties=props,
            )

    def publish_retry(self, original_routing_key: str, body: bytes, expiration_ms: int, batch_id=None):
        """把失败消息发到对应 retry 队列等待 TTL 后回流原队列。"""
        self._ensure()
        rk_retry = f"{original_routing_key}.retry"
        if rk_retry not in self.RETRY_QUEUES:
            raise ValueError(f"no retry queue for routing_key={original_routing_key}")
        props = pika.BasicProperties(
            delivery_mode=2,
            expiration=str(expiration_ms),
            headers={"batch_id": batch_id} if batch_id else {},
        )
        self.channel.basic_publish(
            exchange=self.EXCHANGE, routing_key=rk_retry,
            body=body, properties=props,
        )

    def consume(self, queue: str, callback, prefetch_count: int = 1):
        self._ensure()
        self.channel.basic_qos(prefetch_count=prefetch_count)

        def _callback(ch, method, properties, body):
            try:
                message = json.loads(body)
                message["_delivery_tag"] = method.delivery_tag
                message["_routing_key"] = method.routing_key
                message["_headers"] = (properties.headers or {})
                callback(message)
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except _NackOnce as e:
                if e.requeue:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                else:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception:
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        self.channel.basic_consume(queue=queue, on_message_callback=_callback)

    def consume_dead(self, batch_id: str) -> list[dict]:
        """非消费式拉取 dead.letter 内匹配 batch_id 的死信(basic_get)。"""
        self._ensure()
        out = []
        while True:
            method, props, body = self.channel.basic_get(queue=self.DEAD_LETTER_QUEUE, auto_ack=True)
            if method is None:
                break
            headers = (props.headers or {}) if props else {}
            entry = json.loads(body)
            entry["_headers"] = headers
            if headers.get("batch_id") == batch_id:
                out.append(entry)
        return out

    def start_consuming(self):
        self.channel.start_consuming()

    def close(self):
        if self.connection and self.connection.is_open:
            self.connection.close()


class _NackOnce(Exception):
    """callback 通过 raise 此异常控制 ack/nack 行为;比直接 throw Exception 更明确。"""
    def __init__(self, requeue: bool = False):
        self.requeue = requeue
```

注意原 `consume` 的 `_callback` 顶层 try 会捕获所有异常并 nack(requeue=False)。新设计引入 `_NackOnce` 让 callback 显式控制:`raise _NackOnce(requeue=True)` 用于 bulk 非窗口时退回;`raise _NackOnce(requeue=False)` 走 DLQ。

- [ ] **Step 4: Run → PASS**

```bash
cd backend && .venv/bin/pytest tests/test_rabbitmq_queues.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/core/rabbitmq.py tests/test_rabbitmq_queues.py
git commit -m "feat(rabbitmq): 多队列 + DLX + 6 retry 队列 + consume_dead"
```

---

## Task 6: retry.py(bac睡指数退避 + bulk 时段)

**Files:**
- Create: `app/core/retry.py`
- Test: `tests/test_retry_timewindow.py`

**Interfaces:**
- Produces: `app.core.retry.BACKOFFS`,`backoff_for_retry(retry_count)->int_ms`,`is_bulk_window_now()->bool`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_retry_timewindow.py
import pytest
from freezegun import freeze_time
from app.core.retry import backoff_for_retry, is_bulk_window_now


@pytest.mark.parametrize("rc,expected", [(0,10000),(1,60000),(2,600000),(9,600000)])
def test_backoff(rc, expected):
    assert backoff_for_retry(rc) == expected


@pytest.mark.parametrize("hour,start,end,expected", [
    (23, 22, 8, True),   # 跨午夜窗口内
    (2, 22, 8, True),
    (8, 22, 8, False),   # 边界(开区间)
    (12, 22, 8, False),  # 白天窗口外
    (14, 14, 18, True),  # 同日窗口内
    (18, 14, 18, False), # 同日边界
])
def test_bulk_window(hour, start, end, expected, monkeypatch):
    monkeypatch.setenv("BULK_WINDOW_START", str(start))
    monkeypatch.setenv("BULK_WINDOW_END", str(end))
    with freeze_time(f"2026-07-15 {hour:02d}:30:00"):
        # 重新读 env → 重新 import 不能用 importlib(reload 会污染);改实现读 settings
        assert is_bulk_window_now() is expected
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Create retry.py**

```python
# backend/app/core/retry.py
from datetime import datetime
from app.config import settings

BACKOFFS_MS = (10_000, 60_000, 600_000)  # 10s, 1m, 10m


def backoff_for_retry(retry_count: int) -> int:
    """返回下轮重试前等待 ms。retry_count 是已失败次数(0 表示第一次失败)。"""
    idx = min(retry_count, len(BACKOFFS_MS) - 1)
    return BACKOFFS_MS[idx]


def is_bulk_window_now() -> bool:
    """当前是否处于 bulk 允许消费时段(用 settings,不读 env 直接,便于 monkeypatch)。"""
    start = settings.BULK_WINDOW_START
    end = settings.BULK_WINDOW_END
    h = datetime.now().hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end
```

> 注:测试用 `monkeypatch.setenv` 改 env 需让 Settings 重新读;pydantic Settings 是单例 import 时构造。为使测试可靠,`is_bulk_window_now` 改为**每次读 settings**(已实现);但 settings 是模块级单例,monkeypatch.setenv 在它已经构造后无效。
>
> 修正:让 `is_bulk_window_now` 直接从 env 取数以便 monkeypatch 生效:

```python
import os
from datetime import datetime


def is_bulk_window_now() -> bool:
    start = int(os.getenv("BULK_WINDOW_START", "22"))
    end = int(os.getenv("BULK_WINDOW_END", "8"))
    h = datetime.now().hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end
```

(放弃走 settings 的一致性,换取 monkeypatch 可测性。生产 env 由 start.sh 注入,等价。)

`backoff_for_retry` 保持不变。

- [ ] **Step 4: Run → PASS**

```bash
cd backend && .venv/bin/pytest tests/test_retry_timewindow.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/core/retry.py tests/test_retry_timewindow.py
git commit -m "feat(retry): 指数退避表 + bulk 时段窗口函数"
```

---

## Task 7: BatchService(状态机 + 幂等)

**Files:**
- Create: `app/modules/report/batch_service.py`
- Test: `tests/test_batch_service.py`

**Interfaces:**
- Consumes: `BatchImport`/`BatchImportFile`(Task 2),`rabbitmq`(Task 5),`settings`
- Produces: `BatchService` 静态方法集(见 Spec §4.3)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_batch_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.base import Base
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()

# (后续 test cases 见 step 3,实现后一并写)
```

实现 T1.1–T1.7 见 Spec §7.2。具体测试代码:

```python
import pytest
import uuid
from unittest.mock import patch


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.base import Base
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def _mock_publish():
    with patch("app.modules.report.batch_service.rabbitmq") as M:
        msgs = []
        M.publish.side_effect = lambda m: msgs.append(m)
        return M, msgs


def test_create_append_finalize_state_machine(db):
    M, msgs = _mock_publish()
    import os, tempfile
    tmp = tempfile.mkdtemp()
    with patch("app.modules.report.batch_service.settings.FILE_STORAGE_ROOT", tmp):
        b = BatchService.create_batch(db, "H001", "admin", "import.zip")
        assert b.status == "uploading"
        for i in range(3):
            BatchService.append_chunk(db, b.id, i, 3, b"hello")
        BatchService.finalize_batch(db, b.id, None, 3, 15)
        db.refresh(b)
        assert b.status == "extracting"
    assert any(getattr(m, "task_type", "").endswith("extract") or m.task_type == "extract"
               for m in msgs) or len(msgs) == 1


def test_handle_extracted_file_idempotent(db):
    db.add(BatchImport(id="b1", hospital_id="H", user_id="u", filename="x", archive_path="/x"))
    db.commit()
    fid1 = BatchService.handle_extracted_file(db, "b1", "a.pdf", "abc12345", 10)
    fid2 = BatchService.handle_extracted_file(db, "b1", "a.pdf", "abc12345", 10)
    assert fid1 == fid2  # 同 (batch,crc32) 返回同 id;total 不增加
    assert db.query(BatchImportFile).count() == 1


def test_increment_progress_idempotent(db):
    db.add(BatchImport(id="b1", hospital_id="H", user_id="u", filename="x", archive_path="/x"))
    db.add(BatchImportFile(id="f1", batch_id="b1", file_path="a", file_size=1, crc32="abc12345"))
    db.commit()
    BatchService.increment_progress(db, "b1", "f1", "parsed_ok")
    BatchService.increment_progress(db, "b1", "f1", "parsed_ok")  # 重复应不增加
    b = db.query(BatchImport).get("b1")
    assert b.parsed_ok == 1


def test_retry_failed_requeues(db):
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x", archive_path="/x", status="partial_failed", failed=1)
    f = BatchImportFile(id="f1", batch_id="b1", file_path="/x/a.pdf", file_size=1, crc32="abc12345", status="failed", error_message="x")
    db.add_all([b, f]); db.commit()
    M, msgs = _mock_publish()
    r = BatchService.retry_failed(db, "b1")
    assert r["requeued"] == 1
    db.refresh(f)
    assert f.status == "queued"


def test_status_complete_or_partial(db):
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x", archive_path="/x", total=2, parsed_ok=1, interp_ok=1)
    db.add(b); db.commit()
    BatchService._maybe_advance_status(db, b)
    db.refresh(b)
    assert b.status == "completed"
    b.failed = 1; b.interp_ok = 0; b.parsed_ok = 1
    BatchService._maybe_advance_status(db, b)
    db.refresh(b)
    assert b.status == "partial_failed"
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement batch_service.py**

```python
# backend/app/modules/report/batch_service.py
import os
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.modules.report.batch_models import BatchImport, BatchImportFile


class BatchService:

    @staticmethod
    def create_batch(db: Session, hospital_id: str, user_id: str, filename: str) -> BatchImport:
        bid = uuid.uuid4().hex
        storage_dir = os.path.join(settings.FILE_STORAGE_ROOT, hospital_id, "batch")
        os.makedirs(storage_dir, exist_ok=True)
        archive_path = os.path.join(storage_dir, f"{bid}.zip")
        b = BatchImport(
            id=bid, hospital_id=hospital_id, user_id=user_id,
            filename=filename, archive_path=archive_path, status="uploading",
        )
        db.add(b); db.commit(); db.refresh(b)
        return b

    @staticmethod
    def append_chunk(db: Session, batch_id: str, index: int, total: int,
                     chunk: bytes) -> int:
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            raise ValueError("batch not found")
        if b.status != "uploading":
            raise ValueError(f"batch not uploading (status={b.status})")
        part_dir = os.path.dirname(b.archive_path)
        part_path = os.path.join(part_dir, f"{batch_id}.part{index}")
        with open(part_path, "wb") as f:
            f.write(chunk)
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        return os.path.getsize(part_path)

    @staticmethod
    def finalize_batch(db: Session, batch_id: str, expected_crc32: Optional[str],
                       expected_total: int, expected_size: int) -> None:
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            raise ValueError("batch not found")
        # 校验总大小
        if expected_size > settings.BATCH_ARCHIVE_MAX_SIZE:
            b.status = "cancelled"
            b.error_message = "archive_too_large"
            db.commit()
            raise ValueError("archive_too_large")
        # 拼装分片为 .zip
        part_dir = os.path.dirname(b.archive_path)
        got_indices = sorted(
            int(fn.split(".part")[-1])
            for fn in os.listdir(part_dir)
            if fn.startswith(f"{batch_id}.part")
        )
        if got_indices != list(range(expected_total)):
            b.status = "cancelled"
            b.error_message = "chunks_incomplete"
            db.commit()
            raise ValueError("chunks_incomplete")
        with open(b.archive_path, "wb") as out:
            crc = 0
            import zlib
            for i in got_indices:
                with open(os.path.join(part_dir, f"{batch_id}.part{i}"), "rb") as part:
                    data = part.read()
                    out.write(data)
                    crc = zlib.crc32(data, crc)
            crc_hex = f"{crc & 0xffffffff:08x}"
        if expected_crc32 and crc_hex != expected_crc32.lower():
            b.status = "cancelled"
            b.error_message = "crc_mismatch"
            db.commit()
            raise ValueError("crc_mismatch")
        # 删分片
        for i in got_indices:
            try:
                os.remove(os.path.join(part_dir, f"{batch_id}.part{i}"))
            except OSError:
                pass
        b.status = "extracting"
        db.commit()
        BatchService.publish_extract_task(batch_id, b.hospital_id, b.archive_path)

    @staticmethod
    def publish_extract_task(batch_id: str, hospital_id: str, archive_path: str):
        rabbitmq.publish(TaskMessage(
            task_type="extract", hospital_id=hospital_id, priority="bulk",
            payload={"batch_id": batch_id, "archive_path": archive_path},
        ))

    @staticmethod
    def handle_extracted_file(db: Session, batch_id: str, file_path: str,
                               crc32: str, file_size: int) -> str:
        """幂等去重:同 (batch_id,crc32) 返回已存在 file_id,不重复记账。"""
        existing = db.query(BatchImportFile).filter_by(
            batch_id=batch_id, crc32=crc32,
        ).first()
        if existing:
            return existing.id
        fid = uuid.uuid4().hex
        db.add(BatchImportFile(
            id=fid, batch_id=batch_id, file_path=file_path,
            file_size=file_size, crc32=crc32, status="queued",
        ))
        db.commit()
        return fid

    @staticmethod
    def increment_progress(db: Session, batch_id: str, file_id: str, field: str) -> None:
        """field ∈ {'parsed_ok','interp_ok','failed'}。幂等:靠 file.status 守护只 ++ 一次。"""
        f = db.query(BatchImportFile).get(file_id)
        if f is None:
            return
        new_state = {
            "parsed_ok": "parsed",
            "interp_ok": "interp_ok",
            "failed": "failed",
        }[field]
        # 使用条件 UPDATE 保证只推进一次
        result = db.query(BatchImportFile).filter(
            BatchImportFile.id == file_id,
            BatchImportFile.status.notin_(["parsed", "interp_ok", "failed"]),
        ).update({BatchImportFile.status: new_state})
        if result == 0:
            db.commit()
            return  # 已推进过,不重复
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            db.commit()
            return
        setattr(b, field, (getattr(b, field) or 0) + 1)
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        BatchService._maybe_advance_status(db, b)

    @staticmethod
    def _maybe_advance_status(db: Session, b: BatchImport) -> None:
        if b.status in ("completed", "partial_failed", "cancelled"):
            return
        if b.total <= 0:
            return
        if b.parsed_ok + b.interp_ok + b.failed < b.total:
            return
        if b.failed == 0:
            b.status = "completed"
        else:
            b.status = "partial_failed"
        b.completed_at = datetime.now(timezone.utc)
        db.commit()

    @staticmethod
    def get_progress(db: Session, batch_id: str) -> dict:
        b = db.query(BatchImport).get(batch_id)
        if b is None:
            raise ValueError("batch not found")
        files = db.query(BatchImportFile).filter_by(batch_id=batch_id).all()
        failing = [f for f in files if f.status == "failed"]
        return {
            "batch": {
                "id": b.id, "filename": b.filename, "status": b.status,
                "total": b.total, "parsed_ok": b.parsed_ok,
                "interp_ok": b.interp_ok, "failed": b.failed,
                "error_message": b.error_message,
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "completed_at": b.completed_at.isoformat() if b.completed_at else None,
            },
            "failing_files": [
                {"id": f.id, "file_path": f.file_path, "error_message": f.error_message}
                for f in failing
            ],
        }

    @staticmethod
    def retry_failed(db: Session, batch_id: str, file_ids: Optional[list] = None) -> dict:
        b = db.query(BatchImport).get(batch_id)
        if b is None or b.status == "cancelled":
            raise ValueError("batch not retryable")
        q = db.query(BatchImportFile).filter_by(batch_id=batch_id, status="failed")
        if file_ids:
            q = q.filter(BatchImportFile.id.in_(file_ids))
        files = q.all()
        requeued = 0
        for f in files:
            f.status = "queued"
            f.error_message = None
            if f.report_task_id:
                # 把对应 report_task 重置为 queued 并重投
                from app.modules.report.models import ReportTask
                t = db.query(ReportTask).get(f.report_task_id)
                if t:
                    t.status = "queued"
                    t.retry_count = 0
                    rabbitmq.publish(TaskMessage(
                        task_type="parsing", hospital_id=b.hospital_id,
                        priority="bulk",
                        payload={"task_id": t.id, "hospital_id": b.hospital_id,
                                 "file_path": t.original_file_path, "batch_id": batch_id,
                                 "file_id": f.id},
                    ))
            requeued += 1
        # 已记账的失败数同步回退
        b.failed = max(0, (b.failed or 0) - requeued)
        if b.status == "partial_failed":
            b.status = "parsing"
        b.updated_at = datetime.now(timezone.utc)
        db.commit()
        return {"requeued": requeued}
```

- [ ] **Step 4: Run → PASS**

```bash
cd backend && .venv/bin/pytest tests/test_batch_service.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/modules/report/batch_service.py tests/test_batch_service.py
git commit -m "feat(batch): BatchService 状态机 + 幂等去重 + 进度推进"
```

---

## Task 8: extract_worker(解压 + 幂等 + 入队)

**Files:**
- Create: `app/modules/report/extract_worker.py`
- Test: `tests/test_extract_worker.py`

**Interfaces:**
- Consumes: `BatchService`(Task 7),`rabbitmq`(Task 5),`settings`,`report.service.create_task`
- Produces: `handle_extract_task(message)`,`start_worker()`

- [ ] **Step 1: Write the failing test**(见 Spec §7.2 T2.1–T2.7)

```python
# backend/tests/test_extract_worker.py
import os, io, zipfile, tarfile, pytest, tempfile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models.base import Base
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService


@pytest.fixture
def db_and_dir():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    tmp = tempfile.mkdtemp()
    with patch("app.modules.report.batch_service.settings.FILE_STORAGE_ROOT", tmp), \
         patch("app.modules.report.extract_worker.settings.FILE_STORAGE_ROOT", tmp), \
         patch("app.modules.report.batch_service.rabbitmq") as Mq, \
         patch("app.modules.report.extract_worker.rabbitmq", Mq):
        s = Session()
        # extract_worker 调用 create_task 会用真 report_task 表(也需建)
        yield s, tmp, Mq
        s.close()
```

(后续 T2.1–T2.7 用例按 Spec §7.2 落实;每个用例构造 zip/tar,调 `handle_extract_task`,断言 BatchImportFile 数量、publish 调用次数、failed 状态。)

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement extract_worker.py**

```python
# backend/app/modules/report/extract_worker.py
import os
import zlib
import zipfile
import tarfile
from app.config import settings
from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.modules.report.batch_models import BatchImport, BatchImportFile
from app.modules.report.batch_service import BatchService

ALLOWED_EXTS = {"pdf", "doc", "jpg", "jpeg", "png"}  # 不含 docx (Spec F8)


def handle_extract_task(message: dict):
    payload = message.get("payload", {})
    batch_id = payload.get("batch_id")
    hospital_id = payload.get("hospital_id")
    archive_path = payload.get("archive_path")

    db = next(get_hospital_db(hospital_id))
    try:
        b = db.query(BatchImport).get(batch_id)
        if b is None or b.status == "cancelled":
            return  # 已取消,ack 跳过
        if b.status not in ("extracting", "parsing"):
            # 已完成或正在 parse,只补差
            pass
        try:
            _extract_and_enqueue(db, b, hospital_id, archive_path)
        except (zipfile.BadZipFile, tarfile.TarError, EOFError) as e:
            b.status = "partial_failed"
            b.error_message = f"archive_corrupt: {e}"
            db.commit()
            return
        b = db.query(BatchImport).get(batch_id)
        total = db.query(BatchImportFile).filter_by(batch_id=batch_id).count()
        b.total = total
        if total == 0:
            b.status = "partial_failed"
            b.error_message = "no_valid_files"
        else:
            b.status = "parsing"
        db.commit()
    finally:
        db.close()


def _extract_and_enqueue(db, b, hospital_id, archive_path):
    archive_size = os.path.getsize(archive_path)
    cum_uncompressed = 0
    # 统一迭代器:zip 或 tar
    if archive_path.endswith((".zip", ".ZIP")):
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = os.path.basename(info.filename)
                if name.startswith(".") or "__MACOSX" in info.filename:
                    continue
                ext = os.path.splitext(name)[1].lower().lstrip(".")
                if ext not in ALLOWED_EXTS:
                    continue
                if info.file_size > settings.BATCH_FILE_MAX_SIZE:
                    BatchService.handle_extracted_file.__name__  # noqa
                    f = _record_oversize(db, b.id, info.filename, info.file_size)
                    continue
                cum_uncompressed += info.file_size
                if cum_uncompressed > 5 * archive_size:
                    f = _record_oversize(db, b.id, info.filename, info.file_size)
                    continue  # zip bomb 防护:跳过疑似
                with zf.open(info) as fh:
                    _stream_to_report(db, b, hospital_id, info.filename, fh, info.file_size)
    else:
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = os.path.basename(member.name)
                if "__MACOSX" in member.name or name.startswith("."):
                    continue
                ext = os.path.splitext(name)[1].lower().lstrip(".")
                if ext not in ALLOWED_EXTS:
                    continue
                if member.size > settings.BATCH_FILE_MAX_SIZE:
                    _record_oversize(db, b.id, member.name, member.size)
                    continue
                cum_uncompressed += member.size
                if cum_uncompressed > 5 * archive_size:
                    _record_oversize(db, b.id, member.name, member.size)
                    continue
                fh = tf.extractfile(member)
                _stream_to_report(db, b, hospital_id, member.name, fh, member.size)


def _record_oversize(db, batch_id, file_path, size):
    """记一行 failed='oversize' 但不投 parsing。"""
    fid = BatchService.handle_extracted_file(db, batch_id, file_path,
                                              f"ovs{size:08x}", size)
    f = db.query(BatchImportFile).get(fid)
    if f.status == "queued":
        f.status = "failed"
        f.error_message = "oversize"
        b = db.query(BatchImport).get(batch_id)
        b.failed = (b.failed or 0) + 1
        db.commit()


def _stream_to_report(db, b, hospital_id, rel_path, fh, size):
    """读流,算 crc32,落盘到 storage/hospital/batch/<batch_id>/extracted/<uuid>.<ext>,建 report_task。"""
    data = fh.read()
    crc = f"{zlib.crc32(data) & 0xffffffff:08x}"
    fid = BatchService.handle_extracted_file(db, b.id, rel_path, crc, size)
    f = db.query(BatchImportFile).get(fid)
    if f.status != "queued":
        return  # 已存在(幂等命中),不再 publish
    # 落盘
    ext = os.path.splitext(rel_path)[1].lstrip(".")
    extract_dir = os.path.join(os.path.dirname(b.archive_path), "extracted", b.id)
    os.makedirs(extract_dir, exist_ok=True)
    disk_path = os.path.join(extract_dir, f"{fid}.{ext}")
    with open(disk_path, "wb") as out:
        out.write(data)
    file_type = {"pdf": "pdf", "doc": "docx", "jpg": "image",
                 "jpeg": "image", "png": "image"}[ext]
    from app.modules.report.service import create_task
    # 注意: create_task 在 Task 9 改造为支持 priority='bulk' + batch_id + file_id
    task = create_task(
        db=db, hospital_id=hospital_id, user_id=int(b.user_id) if str(b.user_id).isdigit() else 0,
        file_path=disk_path, filename=os.path.basename(rel_path),
        file_type=file_type, file_size=size, priority="bulk",
        batch_id=b.id, file_id=fid,
    )
    f.report_task_id = task.id
    db.commit()


def start_worker():
    while True:
        try:
            rabbitmq.consume("extract.bulk", handle_extract_task, prefetch_count=1)
            print("Extract worker started")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Extract worker disconnected: {e}, reconnect in 3s")
            import time; time.sleep(3)
```

**注意**:`rabbitmq.consume("extract.bulk", ...)` —— `extract` 任务只有 bulk 一种优先级。Task 5 的 QUEUES 没含 `extract.bulk`,**必须**在 Task 5 的 QUEUES 中补一条 `"extract.bulk": "extract.bulk"`,并在 `_ensure_resources` 中统一声明(DLX 也对它生效)。**对 Task 5 的修正在 Step 4 中做**。

- [ ] **Step 4: Patch rabbitmq.py 加 extract.bulk 队列**

在 `QUEUES` 字典追加:

```python
    "extract.bulk": "extract.bulk",
```

对应 `test_rabbitmq_queues.py` 的断言同步加 `"extract.bulk"`。再次跑 `tests/test_rabbitmq_queues.py` 与 `tests/test_extract_worker.py`。

- [ ] **Step 5: Run → PASS**

```bash
cd backend && .venv/bin/pytest tests/test_extract_worker.py tests/test_rabbitmq_queues.py -v
```

- [ ] **Step 6: Commit**

```bash
git add app/modules/report/extract_worker.py app/core/rabbitmq.py tests/test_extract_worker.py tests/test_rabbitmq_queues.py
git commit -m "feat(batch): extract_worker 解压 + 幂等 + publish parsing.bulk"
```

---

## Task 9: report/service.py 接入 bulk/batch + 删立即重投;report/worker.py per-queue

**Files:**
- Modify: `app/modules/report/service.py`(`create_task` 签名;`process_task` 失败分支;publish routing)
- Modify: `app/modules/report/worker.py`(per-queue consume + bulk 时段 + retry 消费)
- Test: `tests/test_report_worker_bulk.py`

**变更要点**:

1. `create_task` 新增参数 `priority: str = "normal"`、`batch_id: str | None`、`file_id: str | None`。原 `priority: int = 0`(bool) 改为 str(i.e. 破坏式签名变更——因仅内部调用,直接改;现有调用点在 `report/router.py:upload` 与 `batch_service.retry_failed`,同步改)。
2. `process_task` 失败时不再"立即重投 parsing.*",改为根据 retry_count + backoff 走 `publish_retry` 或 DLQ:`retry_count >= 3` → `nack(requeue=False)` 走 DLQ;否则 `publish_retry(current_routing_key, body, backoff_ms)`。但**注**:worker 的 callback 才是控制 ack/nack 的地方;`process_task` 不能直接 nack。`process_task` 失败时应 raise 异常,worker callback 捕获后按 retry_count 决定 publish_retry 或 re-raise 让 `_callback` 走 DLQ。
3. `report/worker.py`:拆 `start_worker` 为分别 consume `parsing.urgent`/`.normal`/`.bulk`,其中 `.bulk` callback 起手先 `is_bulk_window_now()` 判断,非窗口 `raise _NackOnce(requeue=True)`。

**关键代码**:

`service.py` 改 `create_task` 签名和 publish:

```python
def create_task(db, hospital_id, user_id, file_path, filename, file_type, file_size,
                thumbnail_path=None, priority="normal",
                batch_id=None, file_id=None):
    task = ReportTask(... status="queued", priority=0 if priority == "normal" else 1)
    ...
    db.commit(); db.refresh(task)
    report = ReportInfo(task_id=task.id, user_id=user_id)
    db.add(report); db.commit()
    rabbitmq.publish(TaskMessage(
        task_type="parsing", hospital_id=hospital_id, priority=priority,
        payload={"task_id": task.id, "hospital_id": hospital_id,
                 "file_path": file_path, "batch_id": batch_id, "file_id": file_id},
    ))
    return task
```

`process_task` 失败分支改为(删重投):

```python
    except Exception as e:
        task.retry_count += 1
        task.error_message = str(e)
        if task.retry_count >= 3:
            task.status = "failed"
            # file failed 计数由 worker 在 ack/nack 后回写 BatchImportFile
        else:
            task.status = "queued"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
        # 重试决策交给 worker (走 publish_retry 延迟)
        raise  # 让 worker callback 看到异常
```

`worker.py` 改写:

```python
import json
from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq, _NackOnce
from app.core.retry import backoff_for_retry, is_bulk_window_now
from app.modules.report.service import process_task, get_task_status
from app.modules.report.batch_models import BatchImportFile
from app.modules.report.batch_service import BatchService


def handle_parsing_task(message: dict):
    routing_key = message.get("_routing_key", "parsing.normal")
    # bulk 时段过滤
    if routing_key.endswith(".bulk") and not is_bulk_window_now():
        raise _NackOnce(requeue=True)
    payload = message.get("payload", {})
    task_id = payload.get("task_id")
    hospital_id = payload.get("hospital_id")
    batch_id = payload.get("batch_id")
    file_id = payload.get("file_id")

    db = next(get_hospital_db(hospital_id))
    try:
        task = get_task_status(db, task_id)
        if task and task.status == "completed":
            return
        if task and task.status == "queued" and task.retry_count > 0:
            # 来自 retry 队列的回流,放行
            pass
        try:
            process_task(db, task_id, hospital_id)
            # 成功 → 计 batch file 进度(parsed_ok)
            if batch_id and file_id:
                BatchService.increment_progress(db, batch_id, file_id, "parsed_ok")
        except Exception as e:
            # 失败:看 retry_count 决定走 retry 还是 DLQ
            task = get_task_status(db, task_id)
            retries = task.retry_count if task else 0
            if retries >= 3:
                # 走 DLQ;同时回写 file failed
                if batch_id and file_id:
                    try:
                        f = db.query(BatchImportFile).get(file_id)
                        if f and f.status not in ("failed","parsed","interp_ok"):
                            BatchService.increment_progress(db, batch_id, file_id, "failed")
                    except Exception:
                        pass
                raise  # 让 _callback nack(requeue=False) → DLQ
            else:
                # 走延迟队列
                body = json.dumps({
                    "task_type": "parsing",
                    "hospital_id": hospital_id,
                    "payload": payload,
                }).encode()
                rabbitmq.publish_retry(
                    routing_key, body,
                    expiration_ms=backoff_for_retry(retries - 1),
                    batch_id=batch_id,
                )
                return  # ack 当前消息(retry 队列里已存副本)
    finally:
        db.close()


def start_worker():
    while True:
        try:
            rabbitmq.consume("parsing.urgent", handle_parsing_task)
            rabbitmq.consume("parsing.normal", handle_parsing_task)
            rabbitmq.consume("parsing.bulk", handle_parsing_task)
            print("Report parsing worker started (urgent+normal+bulk)")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Parsing worker disconnected: {e}, reconnect in 3s")
            import time; time.sleep(3)
```

- [ ] **Step 1-5: 按 TDD**:先写 worker 时段与重试的测试(mock rabbitmq.consume / publish_retry),再落代码。

测试覆盖:
- bulk 非窗口时 callback 抛 `_NackOnce(requeue=True)`
- normal 队列不受时段影响
- 失败 retry_count=1 → 调 publish_retry(routing_key=原, expiration=backoff(0))
- 失败 retry_count=3 → 抛异常(让 _callback 走 DLQ)+ 回写 file failed
- 成功 + batch_id/file_id → 调 increment_progress(parsed_ok)

- [ ] **Step 6: Sync 改 `report/router.py:upload_report`**

`service.create_task(...)` 调用已用默认 `priority="normal"`;原 router 调用没传 priority,兼容。**只需**确认原 `priority=0` 隐式 → str "normal"。无需改 router。

- [ ] **Step 7: Run tests → PASS;Commit**

```bash
cd backend && .venv/bin/pytest tests/test_report_worker_bulk.py tests/ -v
git add app/modules/report/service.py app/modules/report/worker.py tests/test_report_worker_bulk.py
git commit -m "feat(report): create_task 支持 bulk/batch_id; worker per-queue + retry 队列"
```

---

## Task 10: interpretation/worker.py per-queue + 删 sleep + running 跳过

**Files:**
- Modify: `app/modules/interpretation/worker.py`(参照 `interpretation/worker.py` 现状:已含 `_RETRY_BACKOFFS=(10,20)` 与 `_maybe_requeue_for_retry`,改用延迟队列)

**改动要点**(与 Task 9 同构):

1. `start_worker` 同时 consume `interpretation.urgent`/`.normal`/`.bulk`。
2. `.bulk` callback 起手判断 `is_bulk_window_now()`,非窗口 `raise _NackOnce(requeue=True)`。
3. 删 `_maybe_requeue_for_retry` 的 `time.sleep(backoff)` 阻塞;改 `rabbitmq.publish_retry(routing_key, body, backoff_ms)`。
4. 跳过逻辑:`status in ('running','completed')` 都跳过(原仅跳 completed)。
5. 完成 + batch_id + file_id → `BatchService.increment_progress(db, batch_id, file_id, "interp_ok")`。需在 `interpretation.service` 完成处把 batch_id/file_id 透传过来(从消息 payload 读)。

- [ ] **测试**:同 Task 9 结构,`tests/test_interp_worker_bulk.py`。
- [ ] 重构 `interpretation/worker.py` 与 service 调用点。
- [ ] Run + Commit:`feat(interp): worker per-queue + bulk 时段 + 延迟队列重试 + running 跳过`

---

## Task 11: batch_router.py(8 个 endpoint)

**Files:**
- Create: `app/modules/report/batch_router.py`
- Test: `tests/test_batch_router.py`(httpx AsyncClient + SQLite + mock rabbitmq)

**Interfaces:**
- Consumes: `BatchService`,`rabbitmq.consume_dead`,`get_current_user`(admin role)

- [ ] **Step 1: Write failing tests**(见 Spec §7.2 T6.1–T6.5,用 httpx `AsyncClient` 起测试 app)
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement batch_router.py**

8 个 endpoint 按下表实现(关键:chunk 流式落 `.partN`,complete 校验 CRC+拼装,publish extract.task):

| API | 实现要点 |
|-----|----------|
| `POST /reports/batches` | form `filename`;admin 校验;`BatchService.create_batch` |
| `POST /reports/batches/{bid}/chunk` | form `index,total,data`;`await data.read()` → `append_chunk` |
| `POST /reports/batches/{bid}/complete` | JSON `expected_crc32,expected_total,expected_size`;`finalize_batch`;失败转 400 |
| `GET /reports/batches` | query 分页;按 hospital 过滤 |
| `GET /reports/batches/{bid}` | `BatchService.get_progress` |
| `GET /reports/batches/{bid}/dead` | `rabbitmq.consume_dead(bid)` |
| `POST /reports/batches/{bid}/retry` | JSON `file_ids?`;`BatchService.retry_failed` |
| `POST /reports/batches/{bid}/cancel` | 设 `status='cancelled'`,不允许 cancel completed |

```python
# backend/app/modules/report/batch_router.py
import os
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.core.rabbitmq import rabbitmq
from app.modules.report.batch_service import BatchService


router = APIRouter()


def _db(current_user: CurrentUser = Depends(get_current_user)):
    if not current_user.hospital_id:
        raise HTTPException(400, "Hospital context required")
    if current_user.role != "admin":
        raise HTTPException(403, "admin only")
    gen = get_hospital_db(current_user.hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


@router.post("/batches")
def create_batch(filename: str = Form(...),
                 db: Session = Depends(_db),
                 user: CurrentUser = Depends(get_current_user)):
    b = BatchService.create_batch(db, user.hospital_id, str(user.user_id), filename)
    return {"batch_id": b.id}


@router.post("/batches/{batch_id}/chunk")
async def upload_chunk(batch_id: str,
                      index: int = Form(...),
                      total: int = Form(...),
                      data: UploadFile = File(...),
                      db: Session = Depends(_db)):
    chunk = await data.read()
    BatchService.append_chunk(db, batch_id, index, total, chunk)
    return {"received": index, "total": total}


@router.post("/batches/{batch_id}/complete")
def complete_batch(batch_id: str, body: dict,
                   db: Session = Depends(_db)):
    try:
        BatchService.finalize_batch(
            db, batch_id,
            body.get("expected_crc32"),
            int(body["expected_total"]),
            int(body["expected_size"]),
        )
    except ValueError as e:
        code = str(e)
        status = 400
        if code in ("archive_too_large", "crc_mismatch", "chunks_incomplete"):
            status = 400
        raise HTTPException(status, detail=code)
    return {"batch_id": batch_id, "status": "extracting"}


@router.get("/batches")
def list_batches(page: int = Query(1, ge=1),
                 page_size: int = Query(20, ge=1, le=100),
                 status: Optional[str] = None,
                 db: Session = Depends(_db),
                 user: CurrentUser = Depends(get_current_user)):
    from app.modules.report.batch_models import BatchImport
    q = db.query(BatchImport).filter_by(hospital_id=user.hospital_id)
    if status:
        q = q.filter(BatchImport.status == status)
    total = q.count()
    items = q.order_by(BatchImport.created_at.desc()) \
             .offset((page-1)*page_size).limit(page_size).all()
    return {"items": [{
        "id": b.id, "filename": b.filename, "status": b.status,
        "total": b.total, "parsed_ok": b.parsed_ok, "interp_ok": b.interp_ok,
        "failed": b.failed, "created_at": b.created_at.isoformat() if b.created_at else None,
    } for b in items], "total": total, "page": page, "page_size": page_size}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(_db)):
    try:
        return BatchService.get_progress(db, batch_id)
    except ValueError:
        raise HTTPException(404, "batch not found")


@router.get("/batches/{batch_id}/dead")
def get_dead(batch_id: str, db: Session = Depends(_db)):
    return {"dead": rabbitmq.consume_dead(batch_id)}


@router.post("/batches/{batch_id}/retry")
def retry_batch(batch_id: str, body: dict = {},
                db: Session = Depends(_db)):
    try:
        return BatchService.retry_failed(db, batch_id, body.get("file_ids"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str, db: Session = Depends(_db)):
    from app.modules.report.batch_models import BatchImport
    b = db.query(BatchImport).get(batch_id)
    if b is None:
        raise HTTPException(404, "batch not found")
    if b.status in ("completed", "partial_failed"):
        raise HTTPException(400, f"cannot cancel batch in status={b.status}")
    b.status = "cancelled"
    db.commit()
    return {"cancelled": True}
```

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**`feat(batch): batch_router 8 个 endpoint`

---

## Task 12: BatchSweeper 后台巡检

**Files:**
- Create: `app/core/batch_sweeper.py`
- Modify: `app/main.py`(启动 sweeper)
- Test: `tests/test_batch_sweeper.py`

**Interfaces:**
- Produces: `batch_sweeper.start(loop)` async task 入口,供 `main.py` 启动时 `asyncio.create_task(start())` 调用

- [ ] **Step 1: Failing test**(构造一个 `updated_at < now-30min` 的 batch,assert `_sweep_once` 推进其状态)
- [ ] **Step 2: Run → FAIL**
- [ ] **Step 3: Implement batch_sweeper.py**

```python
# backend/app/core/batch_sweeper.py
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.core.database import get_hospital_db
from app.modules.report.batch_models import BatchImport
from app.modules.report.batch_service import BatchService

log = logging.getLogger("batch_sweeper")


async def start():
    """后台协程,周期巡检卡住的 batch。"""
    while True:
        try:
            await asyncio.sleep(settings.BATCH_SWEEP_INTERVAL)
            _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("sweep error: %s", e)


def _sweep_once():
    threshold = datetime.now(timezone.utc) - timedelta(seconds=settings.BATCH_SWEEP_STALL_THRESHOLD)
    # 遍历所有 hospital db(简化:从 settings/已知 hospital 列表;若无需,只扫 default db)
    # 这里复用 dispatch 模块如果它知道所有 hospital_id;否则仅扫 H001 兜底
    # 保守实现:扫单 db(H001),企业部署需扩展为多 db
    for hospital_id in ("H001",):
        try:
            db = next(get_hospital_db(hospital_id))
            try:
                stuck = db.query(BatchImport).filter(
                    BatchImport.status.in_(("extracting","parsing","interpreting")),
                    BatchImport.updated_at < threshold,
                ).all()
                for b in stuck:
                    BatchService._maybe_advance_status(db, b)
                    # extracting 卡住的 → 触发 extract 续跑(幂等)
                    if b.status == "extracting" and b.updated_at < threshold:
                        BatchService.publish_extract_task(b.id, b.hospital_id, b.archive_path)
            finally:
                db.close()
        except Exception:
            log.exception("sweep for hospital %s failed", hospital_id)
```

> **注**:多 hospital db 的完整 sweep 需要 hospital 列表来源(若 dispatch 模块已有,直接复用)。第一版硬编码 `("H001",)`,企业部署需扩展;在文件顶部 `# TODO` 注明。

- [ ] **Step 4: Run → PASS**
- [ ] **Step 5: Commit**`feat(batch): BatchSweeper 后台巡检`

---

## Task 13: main.py 注册 router + 启动 sweeper

**Files:**
- Modify: `app/main.py:10, 36` 区域

- [ ] **Step 1**: at top imports 加:

```python
from app.modules.report.batch_router import router as batch_router
from app.core.batch_sweeper import start as start_sweeper
```

- [ ] **Step 2**: 在 `include_router(report_router, ...)` 之后加:

```python
    app.include_router(batch_router, prefix="/api/v1/reports", tags=["reports-batch"])
```

- [ ] **Step 3**: 在 `create_app` 返回前加启动 sweeper:

```python
    @app.on_event("startup")
    async def _start_sweeper():
        import asyncio
        app.state.sweeper_task = asyncio.create_task(start_sweeper())
    @app.on_event("shutdown")
    async def _stop_sweeper():
        app.state.sweeper_task.cancel()
```

- [ ] **Step 4: Run** `cd backend && .venv/bin/pytest tests/ -v` 全绿
- [ ] **Step 5: Commit**`feat(app): 注册 batch_router + 启动 BatchSweeper`

---

## Task 14: report/router.py DOCX 移除 + 流式 size 校验

**Files:**
- Modify: `app/modules/report/router.py:15-19, 50-52`

- [ ] **Step 1: Modify whitelist**(去掉 docx):

```python
ALLOWED_TYPES = {
    "pdf": "pdf", "doc": "docx",   # doc 保留但走 docx 路径仍会失败(Spec F8);实际白名单应只留可解析的
    "jpg": "image", "jpeg": "image", "png": "image",
}
```

> 决策:Spec F8 说"DOCX 从批量上传白名单移除"。**单文件** `POST /reports/upload` 仍按现状支持 docx(旧用户行为),worker 失败时已 raise。批量(extract_worker)的 ALLOWED_EXTS 不含 docx(Task 8 已处理)。**单文件 router 白名单保持 docx,不动**。本任务实际只做"流式 size 校验"。

修正:本任务**只做流式 size 校验**(去掉先全读后校验,F1 风险):

```python
    storage_dir = os.path.join(settings.FILE_STORAGE_ROOT, current_user.hospital_id,
                               "reports", str(current_user.user_id))
    os.makedirs(storage_dir, exist_ok=True)
    file_id = uuid.uuid4().hex
    file_path = os.path.join(storage_dir, f"{file_id}.{ext}")
    size = 0
    with open(file_path, "wb") as out:
        while True:
            buf = file.file.read(1024 * 1024)  # 1MB 块
            if not buf:
                break
            size += len(buf)
            if size > MAX_FILE_SIZE:
                out.close()
                os.remove(file_path)
                raise ValidationException(detail="File too large (max 20MB)")
            out.write(buf)
    task = service.create_task(...)
```

- [ ] **Step 2: Test**(构造 25MB 上传,assert 400 + 不留盘;正常 5MB assert 通过)
- [ ] **Step 3: Commit**`fix(upload): 流式 size 校验,避免大文件先读入内存`

---

## Task 15: 迁移脚本 + start.sh DDL + extract worker 启动 + 压测脚本

**Files:**
- Create: `infra/rabbitmq-queue-reset.sh`
- Modify: `start.sh:99-134`(DDL 加两表已在 Task 2 完成;本任务补 extract_worker 启动段 `259-280`)
- Create: `scripts/bench-batch.sh`

- [ ] **Step 1: Create rabbitmq-queue-reset.sh**

```bash
#!/usr/bin/env bash
# infra/rabbitmq-queue-reset.sh
# 迁移用:删除旧队列(无 DLX args),让新代码以带 DLX 的 args 重新声明。
set -e
RABBIT_CONTAINER="${RABBIT_CONTAINER:-hospital-rabbitmq}"

QUEUES=(parsing.urgent parsing.normal interpretation.urgent interpretation.normal dead.letter)
for q in "${QUEUES[@]}"; do
  docker exec "$RABBIT_CONTAINER" rabbitmqctl delete_queue "$q" 2>/dev/null || true
done
echo "Old queues deleted; new code will recreate with DLX args."
```

- [ ] **Step 2: Add extract_worker 启动到 start.sh**

在 `start.sh` 现有 `worker.py` 启动段(`259-280` 区域)后追加:

```bash
# 批量解压 worker (不抢 GPU)
if [[ "$SKIP_OCR" != "1" ]]; then
  log "Starting extract worker..."
  nohup "$VENV" python -c "from app.modules.report.extract_worker import start_worker; start_worker()" \
    >> "$LOG_DIR/extract_worker.log" 2>&1 &
  echo $! > "$PID_DIR/extract_worker.pid"
fi
```

- [ ] **Step 3: Create scripts/bench-batch.sh**(见 Spec §7.6,生成 1000 个小 PDF → zip → curl 流式分片上传 → 轮询 → 每 5s 采 nvidia-smi)
- [ ] **Step 4: chmod +x scripts/bench-batch.sh infra/rabbitmq-queue-reset.sh**
- [ ] **Step 5: Commit**`feat(ops): rabbitmq 迁移脚本 + extract_worker 启动 + 压测脚本`

---

## Task 16: 集成测试(可选,testcontainers-rabbitmq)

**Files:**
- Create: `tests/test_integration_dlq.py`(标记 `@pytest.mark.integration`)

按 Spec §7.2 T5.1–T5.3、T4.3 写:用 `testcontainers` 起 rabbitmq:3.12-management,验真 DLX 流转、TTL drop、延迟队列 TTL 过期回流。**本任务标记为可选**,默认不跑;CI 加 `-m integration` 触发。

依赖 `testcontainers` 已在 Task 1 step 4 提及,如未装则跳过。

---

## Self-Review

**1. Spec coverage**:
- §4.1 数据模型 → Task 2 ✓
- §4.2 HTTP 接口 8 个 → Task 11 ✓
- §4.3 BatchService → Task 7 ✓
- §4.4 extract_worker → Task 8 ✓
- §4.5 medgo_sem → Task 3, wrap → Task 4 ✓
- §4.6 RabbitMQ 多队列 + DLX + retry 队列 → Task 5(+ extract.bulk 补丁 Task 8 step 4)✓
- §4.7 retry backoff → Task 6 ✓;service.py retry 分支 → Task 9 / Task 10 ✓
- §4.8 bulk 时段 → Task 6(函数) + Task 9/10(worker)✓
- §4.9 BatchSweeper → Task 12 ✓
- §4.10 幂等契约 → Task 7/Task 8 ✓
- §5 失败路径 F1-F20 → 散落各 task,F1 流式 Task 14,F2 CRC Task 7,F3/F4 reaper **遗漏** → 见下;F5 Task 8,F6 Task 8,F7 Task 8,F8 Task 8(ALLOWED_EXTS)+ Task 14 决策保留单文件 docx,F9-F11 Task 9 retry 队列,F12 Task 3 sem,F13 rabbitmq 重连沿用,F14 Task 12,F15 running 跳过 Task 10,F16 Task 9/10,F17 Task 11 cancel + Task 8 + sweeper,F18 Task 8 重投幂等,F19 DLQ TTL Task 5 + Task 16,F20 BigInteger Task 2 ✓
- §6 改动清单/迁移 → Task 15 ✓

**遗漏修正**:**F3/F4 reaper(孤儿 uploading 清理)**Spec §5 明确"后台 reaper 每 10min 扫 `status='uploading'` 且 `updated_at < now-2h`"。BatchSweeper(Task 12)当前只扫 extracting/parsing/interpreting,**未含 uploading 兜底**。

→ **对 Task 12 增补**:`_sweep_once` 增加对 `status='uploading'` 且 `updated_at < now - BATCH_CHUNK_TIMEOUT(2h)` 的 batch:删 `.partN` 分片 + 删 BatchImport row + 删 BatchImportFile 关联行。已在 Task 12 implementation 注释里补:`# FUTURE` 标 → 实际应实现,非 future。现修正在 Task 12 的 `_sweep_once` 增加:

```python
# F4 reaper:孤儿 uploading 兜底
reaper_threshold = datetime.now(timezone.utc) - timedelta(seconds=settings.BATCH_CHUNK_TIMEOUT)
orphaned = db.query(BatchImport).filter(
    BatchImport.status == "uploading",
    BatchImport.updated_at < reaper_threshold,
).all()
for b in orphaned:
    import os, glob
    part_dir = os.path.dirname(b.archive_path)
    for p in glob.glob(os.path.join(part_dir, f"{b.id}.part*")):
        try: os.remove(p)
        except OSError: pass
    db.query(BatchImportFile).filter_by(batch_id=b.id).delete()
    db.delete(b)
    db.commit()
```

**2. Placeholder scan**:
- Task 11 "见 Spec §..." 引用是 OK 的(具体表已给);**但 Task 10 的 worker 代码引用了 `interpretation.service` 完成处,**需**指明 batch_id/file_id 透传机制。补:interp service 的入口 `process_interpretation(db, report_id, hospital_id)` 增加 `batch_id=None, file_id=None` 可选参,从消息 payload 透传;在写完 `report_interpretation.status='completed'` 后调 `BatchService.increment_progress(db, batch_id, file_id, 'interp_ok')`。Task 10 step 已含此意;合到 task 描述**需明确**。
- F4 reaper 已修正。

**3. Type consistency**:
- `BatchImport.id`(str/uuid4) vs `ReportTask.id`(BigInteger):`BatchImportFile.report_task_id = Column(BigInteger)`匹配 ReportTask ✓
- `TaskMessage.priority` Task 5 改为 str,调用点 Task 9/10 用字符串 ✓
- `BatchImport.user_id` str(Task 2)与 `service.create_task(user_id: int)`:extract_worker 中转换 `int(b.user_id) if str(b.user_id).isdigit() else 0` ✓
- `rabbitmq.publish_retry(routing_key, body, expiration_ms)` vs Task 9 publish_retry 调用 ✓

→ Self-review 完成,1 项遗漏(F4 reaper)已修正,gap 已补。