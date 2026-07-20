# 日志收口实施计划 — `/data/logs` 月轮转聚合

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 backend 所有 Python logging 收口到 `/data/logs/app.log`,按月轮转 + 永久保留,并对重要步骤 logger 命名做一次统一。

**Architecture:** 新建 `app/core/logging_config.py` 提供纯 stdlib `MonthlyRotatingFileHandler` + `setup_logging()`。在每个 Python 进程入口(FastAPI `create_app` 与 3 个 worker 的 `start_worker()`)调用一次。`start.sh` 把 8 个 `nohup` 重定向目标从 `/tmp/*.log` 改到 `/data/logs/*.stdout.log`。重要步骤 logger 命名重塑(`app.parse` / `app.upload` / `app.interp` / `app.judge` / `app.planner` / `app.batch` / `app.batch.sweeper` / `app.batch.extract`)。Workers 现有 `print()` 不强迁,保留 stdout 双轨。

**Tech Stack:** Python 3.10 stdlib `logging` + `logging.handlers` + `freezegun`(测试)。无新增运行时依赖。

## Global Constraints

- 不引入任何第三方日志库(`concurrent-log-handler` 等);纯 stdlib
- 不改 `backend/pyproject.toml` 运行时依赖(新增 `freezegun` 已在 deps 列表,line 见 `pyproject.toml`)
- `setup_logging()` 优先读 `os.environ["LOG_LEVEL"]` 而非 `Settings` 实例,避免依赖耦合
- 主 venv `backend/.venv`(cu12)与 vLLM/paddle venv 的隔离关系不动
- `start.sh` 只改行内字符串(redirect 目标),不改 GPU 分配逻辑、不改 DB DDL 块、不改 PID 文件命名
- `backend/.venv-vllm-cu12` 与 `backend/paddle_venv` 内部不调 `setup_logging()`(它们是外部服务,不走 backend 的 logging_config)
- workers 的 `print()` 不动 — 与 Python logging 形成 `/data/logs/worker-*.stdout.log` + `/data/logs/app.log` 双轨,具体说明写进 AGENTS.md

## 依赖关系总览(供 implementer 把握接口边界)

| 单元 | 上游依赖 | 下游消费者 |
|------|---------|-----------|
| `logging_config.MonthlyRotatingFileHandler` | stdlib `TimedRotatingFileHandler` | `setup_logging()` |
| `logging_config.setup_logging()` | `MonthlyRotatingFileHandler` | `main.create_app`、3 个 `start_worker()` |
| `main.create_app` 改动 | `setup_logging` | (新增)main 入口 |
| 3 个 `start_worker()` 改动 | `setup_logging` | (新增)workers 入口 |
| 6 处 logger 改名 | 无 | (重命名)所有引用该 logger 名的现有代码 |
| `start.sh` 改动 | 无 | (字符串替换)8 处 redirect 目标 |
| `config.Settings.LOG_LEVEL` | 无 | 文档/自省用,不被 `setup_logging` 消费 |
| `.env.example` | 无 | 文档 |
| `AGENTS.md` 增补 | 无 | 后续 Agent |

---

### Task 1: 新建 `MonthlyRotatingFileHandler` 与 `setup_logging()`

**Files:**
- Create: `backend/app/core/logging_config.py`
- Test: `backend/tests/core/test_logging_config.py`

**Interfaces:**
- Consumes: stdlib `logging`, `logging.handlers.TimedRotatingFileHandler`
- Produces:
  - `class MonthlyRotatingFileHandler(TimedRotatingFileHandler)` — 按 calendar month rollover,suffix `"%Y-%m"`,`backupCount=0`(永久保留)
  - `def setup_logging(default_level: str = "INFO") -> None` — 读 `os.environ["LOG_LEVEL"]`,创建 `/data/logs`,装配 root logger

- [ ] **Step 1.1: Write the failing test for `MonthlyRotatingFileHandler` rollover**

Create `backend/tests/core/test_logging_config.py`:

```python
import logging
import os
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from app.core.logging_config import MonthlyRotatingFileHandler, setup_logging


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_monthly_rollover_renames_to_yyyymm_and_starts_new_file(tmp_path):
    """跨月初一次 rollover:旧文件 rename 为 app.log.<YYYY-MM>,新 app.log 为空等待新写入。"""
    log_file = tmp_path / "app.log"
    handler = MonthlyRotatingFileHandler(str(log_file))
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test_monthly_rollover")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with freeze_time("2026-07-31 23:59:00"):
        logger.info("july-line-1")
    handler.flush()

    # 触发月初 rollover(shouldRollover 检查 record 时间)
    with freeze_time("2026-08-01 00:00:30"):
        logger.info("august-line-1")
    handler.flush()
    handler.close()

    rotated = tmp_path / "app.log.2026-07"
    assert rotated.exists(), "旧月文件应 rename 为 app.log.2026-07"
    assert "july-line-1" in _read(rotated)
    assert "august-line-1" in _read(log_file), "新月第一行应写入新 app.log"
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/core/test_logging_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.logging_config'`

- [ ] **Step 1.3: Implement `MonthlyRotatingFileHandler`**

Create `backend/app/core/logging_config.py`:

```python
"""日志收口配置。

所有 Python 进程入口调用 ``setup_logging()`` 一次,把日志统一写到
``/data/logs/app.log``;按月初切分,旧文件 rename 为 ``app.log.<YYYY-MM>``,
``backupCount=0`` 表示永久保留(运维人工清理)。

纯 stdlib 实现,不引入第三方日志库;主 venv / vLLM venv / paddle venv 的
隔离关系不受影响(只有主 venv 进程会 import 本模块)。
"""
import logging
import os
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any

LOG_DIR = "/data/logs"
LOG_FILE = os.path.join(LOG_DIR, "app.log")
# 永久保留历史月日志(运维人工清理);backupCount=0 在 TimedRotatingFileHandler
# 语义里就是「不删除任何备份文件」。
_BACKUP_COUNT = 0
_FMT = "%(asctime)s | %(levelname)s:%(name)s:%(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _now_ts() -> float:
    return time.time()


class MonthlyRotatingFileHandler(TimedRotatingFileHandler):
    """按 calendar month 切分的 file handler。

    继承 stdlib 的 TimedRotatingFileHandler,但把 rollover 触发条件改为
    「当前时间所在月的下一个月初 1 号 0:00 已过」;suffix 固定为
    ``"%Y-%m"`` 以便运维 grep。backupCount=0 表示永久保留。
    """

    def __init__(self, filename: str, **kwargs: Any) -> None:
        super().__init__(
            filename=filename,
            when="MIDNIGHT",  # 占位;实际切分点由 computeRollover 决定
            interval=1,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            utc=False,
            **kwargs,
        )
        # suffix 格式覆盖父类默认的 "%Y-%m-%d" → "%Y-%m"
        self.suffix = "%Y-%m"

    def computeRollover(self, currentTime: float) -> float:
        """返回「当前时间所在月的下一个月初 1 号 0:00」的 POSIX timestamp。

        与父类不同,我们不按 interval 累加,而是直接对齐到 next month start,
        避免日级 MIDNIGHT 累加在月末跳月时出错。
        """
        t = datetime.fromtimestamp(currentTime)  # local tz
        year = t.year + (1 if t.month == 12 else 0)
        month = 1 if t.month == 12 else t.month + 1
        first_of_next = t.replace(year=year, month=month, day=1,
                                  hour=0, minute=0, second=0, microsecond=0)
        return first_of_next.timestamp()

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        """当前时间是否已越过上次计算的下月起点。"""
        current = _now_ts()
        if self.rotation_at is None or self.rotation_at < 0:
            self.rotation_at = self.computeRollover(current)
        if current >= self.rotation_at:
            return True
        if os.path.exists(self.baseFilename):
            return False
        return True
```

- [ ] **Step 1.4: Run test 1.1 — verify it passes**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/core/test_logging_config.py::test_monthly_rollover_renames_to_yyyymm_and_starts_new_file -v`
Expected: PASS

- [ ] **Step 1.5: Write failing test for `setup_logging()`**

Append to `backend/tests/core/test_logging_config.py`:

```python
def test_setup_logging_creates_dir_and_writes_to_app_log(tmp_path, monkeypatch):
    """setup_logging 把 root handler 装到 LOG_FILE,并 mkdir -p LOG_DIR。"""
    log_dir = tmp_path / "logs"
    log_file = log_dir / "app.log"
    monkeypatch.setattr("app.core.logging_config.LOG_DIR", str(log_dir))
    monkeypatch.setattr("app.core.logging_config.LOG_FILE", str(log_file))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    # 清掉 root handlers,避免 test 间污染
    root = logging.getLogger()
    root.handlers.clear()

    setup_logging("INFO")

    assert log_dir.exists(), "/data/logs 目录应被创建"
    logging.getLogger("probe.logger1").debug("debug-line")
    logging.getLogger("probe.logger1").info("info-line")
    # flush 一下
    for h in root.handlers:
        h.flush()

    content = _read(log_file)
    assert "info-line" in content
    assert "debug-line" in content, "LOG_LEVEL=DEBUG 时 root 应捕获 debug"
    assert " | " in content and "probe.logger1" in content

    # 清理:把 root 还原成空 handlers,避免污染其它测试
    root.handlers.clear()
    root.addHandler(logging.NullHandler())


def test_setup_logging_respects_warning_level(tmp_path, monkeypatch):
    """LOG_LEVEL=WARNING 时 debug/info 不应写入文件。"""
    log_dir = tmp_path / "logs"
    log_file = log_dir / "app.log"
    monkeypatch.setattr("app.core.logging_config.LOG_DIR", str(log_dir))
    monkeypatch.setattr("app.core.logging_config.LOG_FILE", str(log_file))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    root = logging.getLogger()
    root.handlers.clear()

    setup_logging("INFO")  # default 被 env 覆盖为 WARNING
    logging.getLogger("probe2").debug("debug-line-2")
    logging.getLogger("probe2").info("info-line-2")
    logging.getLogger("probe2").warning("warn-line-2")
    for h in root.handlers:
        h.flush()

    content = _read(log_file)
    assert "warn-line-2" in content
    assert "debug-line-2" not in content
    assert "info-line-2" not in content

    root.handlers.clear()
    root.addHandler(logging.NullHandler())
```

- [ ] **Step 1.6: Run test to verify it fails**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/core/test_logging_config.py::test_setup_logging_creates_dir_and_writes_to_app_log -v`
Expected: FAIL with `AttributeError: module 'app.core.logging_config' has no attribute 'setup_logging'`

- [ ] **Step 1.7: Implement `setup_logging()`**

Append to `backend/app/core/logging_config.py`:

```python
def setup_logging(default_level: str = "INFO") -> None:
    """在每个 Python 进程入口调用一次。

    - 优先读 ``os.environ["LOG_LEVEL"]``,否则用 ``default_level``
    - mkdir -p ``LOG_DIR`` (mode=0o775)
    - 给 root logger 装一个 MonthlyRotatingFileHandler,Formatter 含时间戳
    - 失败不抛,回退 StreamHandler 到 stdout,确保进程能启动
    """
    level_name = os.environ.get("LOG_LEVEL", default_level).upper()
    level = getattr(logging, level_name, logging.INFO)

    try:
        os.makedirs(LOG_DIR, mode=0o775, exist_ok=True)
        handler: logging.Handler = MonthlyRotatingFileHandler(LOG_FILE)
    except (PermissionError, OSError) as e:
        # 回退到 stdout,不让日志初始化吞掉进程启动
        import sys
        print(f"[logging_config] cannot open {LOG_FILE}: {e!r}; fallback to stdout",
              flush=True)
        handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(logging.Formatter(_FMT, _DATEFMT))

    root = logging.getLogger()
    # 清掉默认/上次设置的 handlers,避免线程里被多次装饰
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
```

- [ ] **Step 1.8: Run all logging tests — verify they pass**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/core/test_logging_config.py -v`
Expected: 3 tests PASS

- [ ] **Step 1.9: Run regression — verify nothing else broke**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest -x -q 2>&1 | tail -20`
Expected: 现有 186 个测试仍全通过(logging_config.py 模块化做到无侵入)

- [ ] **Step 1.10: Commit**

```bash
cd /data/project/hospitalKnowledgeBase
git add backend/app/core/logging_config.py backend/tests/core/test_logging_config.py
git commit -m "feat(logging): add MonthlyRotatingFileHandler + setup_logging()"
```

---

### Task 2: FastAPI 入口接入 `setup_logging()` + sweeper logger 改名

**Files:**
- Modify: `backend/app/main.py:23-24, 52-53, 64`
- Test: `backend/tests/test_main_wiring.py`(已有,补充)

**Interfaces:**
- Consumes: `app.core.logging_config.setup_logging`
- Produces: `app.main.create_app` 在调用链顶部初始化日志

- [ ] **Step 2.1: Inspect existing `main.py` and `test_main_wiring.py`**

Run: `cd /data/project/hospitalKnowledgeBase && head -30 backend/app/main.py && grep -n "batch_sweeper\|getLogger" backend/app/main.py`
Expected: 看到 `line 53: logging.getLogger("app")` 和 `line 64: logging.getLogger("batch_sweeper")`

- [ ] **Step 2.2: Write failing test for setup_logging being called at create_app**

Append to `backend/tests/test_main_wiring.py` (放在文件尾部):

```python
def test_T14_setup_logging_called_in_create_app(monkeypatch):
    """create_app() 进入时 setup_logging 应被调用一次。"""
    import app.main as main_mod
    calls = {"count": 0}

    def _spy(default_level: str = "INFO"):
        calls["count"] += 1

    monkeypatch.setattr("app.core.logging_config.setup_logging", _spy)
    # refresh main module-level import if needed
    main_mod.create_app()
    assert calls["count"] >= 1, "create_app 必须调用 setup_logging()"


def test_T15_sweeper_logger_namespaced_under_app_batch(monkeypatch):
    """main.py 中 sweeper 的 done callback 应使用 app.batch.sweeper logger。"""
    import app.main as main_mod

    monkeypatch.setattr("app.core.batch_sweeper.start", lambda: asyncio.sleep(0))
    # 关掉 ensure_milvus_started 避免真连 milvus
    monkeypatch.setattr("app.ai.config.ensure_milvus_started", lambda: None)

    import logging
    names_seen = set()
    orig_get = logging.getLogger

    def _spy_get(name=None):
        if name and "batch" in name:
            names_seen.add(name)
        return orig_get(name)

    monkeypatch.setattr(logging, "getLogger", _spy_get)
    main_mod.create_app()
    assert "app.batch.sweeper" in names_seen, (
        "sweeper 应使用 app.batch.sweeper 命名空间,实际见到:%r" % names_seen
    )
```

- [ ] **Step 2.3: Run tests to verify they fail**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/test_main_wiring.py::test_T14_setup_logging_called_in_create_app tests/test_main_wiring.py::test_T15_sweeper_logger_namespaced_under_app_batch -v`
Expected: FAIL — `setup_logging` not called / `"batch_sweeper"` 仍是旧名

- [ ] **Step 2.4: Edit `main.py` — call `setup_logging()` + rename sweeper logger**

File: `backend/app/main.py`

Change lines 1-7 (import block) by adding the new import. Edit the imports:

```python
import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.logging_config import setup_logging
from app.api.health import router as health_router
```

Change `create_app()` first lines (line 23-24) to:

```python
def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)
```

Change the global exception handler (lines 52-53):

```python
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logging.getLogger("app.batch.sweeper.parent").exception(  # 占位,下一 edit 修复
            "Unhandled exception on %s %s", request.method, request.url.path,
        )
```

(下一 Step 修正上面占位。这里先让它能 run,后续 edit 把 logger 名改对。)

> Actually no — let's do it in one Edit. Skip the placeholder. Replace lines 50-76 directly in this step with the correct names.

Re-edit lines 50-76:

```python
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        import logging
        logging.getLogger("app").exception(
            "Unhandled exception on %s %s", request.method, request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "code": "INTERNAL_ERROR"},
        )

    @app.on_event("startup")
    async def _start_batch_sweeper():
        import logging
        _sweeper_log = logging.getLogger("app.batch.sweeper")

        def _on_sweeper_done(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                _sweeper_log.error("BatchSweeper task exited unexpectedly: %r", exc)

        task = asyncio.create_task(start_sweeper())
        task.add_done_callback(_on_sweeper_done)
        app.state.batch_sweeper_task = task
```

(其余行保持不变。)

- [ ] **Step 2.5: Run tests — verify they pass**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/test_main_wiring.py -v`
Expected: All tests PASS (含新增 T14/T15)

- [ ] **Step 2.6: Run wider regression**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest -x -q 2>&1 | tail -10`
Expected: 全 PASS

- [ ] **Step 2.7: Commit**

```bash
cd /data/project/hospitalKnowledgeBase
git add backend/app/main.py backend/tests/test_main_wiring.py
git commit -m "feat(logging): wire setup_logging into create_app; rename sweeper logger to app.batch.sweeper"
```

---

### Task 3: Worker 入口接入 `setup_logging()` + 重要步骤 logger 改名

**Files:**
- Modify: `backend/app/modules/report/extract_worker.py:18, 211-219`
- Modify: `backend/app/modules/report/worker.py:1-9, 61-72`
- Modify: `backend/app/modules/interpretation/worker.py:1-9, 95-106`
- Test: `backend/tests/test_report_worker_bulk.py`(已有,补充一个 import smoke test)

**Interfaces:**
- Consumes: `app.core.logging_config.setup_logging`
- Produces: 3 个 `start_worker()` 顶部调用 `setup_logging()`,各文件 logger 名重塑

> Logger 命名决策(本计划定稿,见 spec「最终名称以实施计划为准」):
> - `extract_worker.py` 中的 `"extract_worker"` → `app.batch.extract`(语义匹配 start.sh 中的「批量解压 Worker」)
> - `report/worker.py` 顶部新增 `app.parse` logger(预留;现有 print() 不动)
> - `interpretation/worker.py` 顶部新增 `app.interp.worker` logger(预留;现有 print() 不动 — `app.interp` 根空间留给 `interp_graph.py`,worker 用 `app.interp.worker` 子空间避免层次冲突)

- [ ] **Step 3.1: Write failing test — extract_worker logger renamed and setup_logging called**

Append to `backend/tests/test_extract_worker.py`(若文件不存在则创建):

```python
def test_extract_worker_logger_namespaced_under_app_batch(monkeypatch):
    """extract_worker.py 模块级 logger 应为 app.batch.extract。"""
    import importlib
    import app.modules.report.extract_worker as mod
    assert mod._log.name == "app.batch.extract", (
        f"expected app.batch.extract, got {mod._log.name}"
    )


def test_extract_worker_start_worker_calls_setup_logging(monkeypatch):
    """start_worker() 第一行应调用 setup_logging()。"""
    import app.modules.report.extract_worker as mod

    calls = {"n": 0}
    def _spy(default_level="INFO"):
        calls["n"] += 1
    monkeypatch.setattr("app.core.logging_config.setup_logging", _spy)

    # 让 start_worker 跑一次循环就退出
    import itertools
    counter = itertools.count()
    def _consume(*a, **kw):
        raise RuntimeError("stop-loop")
    def _start_consuming():
        raise RuntimeError("stop-loop")
    monkeypatch.setattr(mod.rabbitmq, "consume", _consume)
    monkeypatch.setattr(mod.rabbitmq, "start_consuming", _start_consuming)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    try:
        mod.start_worker()
    except RuntimeError:
        pass
    assert calls["n"] >= 1, "start_worker 必须先调 setup_logging()"
```

- [ ] **Step 3.2: Run extract_worker tests — verify they fail**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/test_extract_worker.py -v 2>&1 | tail -20`
Expected: FAIL — `_log.name` 仍是 `extract_worker`,setup_logging 未被调

- [ ] **Step 3.3: Edit `extract_worker.py` — rename logger + call setup_logging**

File: `backend/app/modules/report/extract_worker.py`

Replace line 18:

```python
_log = logging.getLogger("extract_worker")
```

with:

```python
_log = logging.getLogger("app.batch.extract")
```

Replace `start_worker()` (lines 211-219) — 加 `setup_logging()` 一行,并在顶部 import:

在 line 11-12 区域已有 `from app.config import settings` 前后,加 import:

```python
from app.config import settings
from app.core.logging_config import setup_logging
from app.core.database import get_hospital_db
```

Replace lines 211-219:

```python
def start_worker():
    setup_logging()
    while True:
        try:
            rabbitmq.consume("extract.bulk", handle_extract_task, prefetch_count=1)
            print("Extract worker started")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Extract worker disconnected: {e}, reconnect in 3s")
            time.sleep(3)
```

- [ ] **Step 3.4: Run extract_worker tests — verify they pass**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/test_extract_worker.py -v 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 3.5: Write failing test — report parsing worker wires setup_logging + app.parse logger**

Append to `backend/tests/test_report_worker_bulk.py`(若不存在则创建):

```python
def test_report_worker_has_app_parse_logger():
    """report/worker.py 应预留 app.parse logger。"""
    import app.modules.report.worker as mod
    assert mod._log.name == "app.parse"


def test_report_worker_start_worker_calls_setup_logging(monkeypatch):
    import app.modules.report.worker as mod
    calls = {"n": 0}
    monkeypatch.setattr("app.core.logging_config.setup_logging",
                        lambda d="INFO": calls.__setitem__("n", calls["n"] + 1))
    # 让 consume/start_consuming 抛错以跳出死循环
    monkeypatch.setattr(mod.rabbitmq, "consume", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(mod.rabbitmq, "start_consuming", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda *_: None)
    try:
        mod.start_worker()
    except RuntimeError:
        pass
    assert calls["n"] >= 1
```

- [ ] **Step 3.6: Run tests — verify they fail**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/test_report_worker_bulk.py -v 2>&1 | tail -20`
Expected: FAIL — `mod._log` not defined

- [ ] **Step 3.7: Edit `report/worker.py` — add logger + setup_logging**

File: `backend/app/modules/report/worker.py`

Replace lines 1-9(import 区):

```python
import json
import logging

from app.core.database import get_hospital_db
from app.core.logging_config import setup_logging
from app.core.rabbitmq import rabbitmq, _NackOnce
from app.core.retry import backoff_for_retry, is_bulk_window_now
from app.modules.report.service import process_task, get_task_status
from app.modules.report.batch_models import BatchImportFile
from app.modules.report.batch_service import BatchService

_log = logging.getLogger("app.parse")
```

Replace `start_worker()`(line 61-72)加 `setup_logging()`:

```python
def start_worker():
    setup_logging()
    while True:
        try:
            rabbitmq.consume("parsing.urgent", handle_parsing_task)
            rabbitmq.consume("parsing.normal", handle_parsing_task)
            rabbitmq.consume("parsing.bulk", handle_parsing_task)
            print("Report parsing worker started (urgent+normal+bulk)")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Parsing worker disconnected: {e}, reconnect in 3s")
            import time
            time.sleep(3)
```

- [ ] **Step 3.8: Run tests — verify they pass**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/test_report_worker_bulk.py -v 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 3.9: Write failing test — interpretation worker wires setup_logging + app.interp.worker logger**

Append to `backend/tests/test_interp_worker_bulk.py`(若不存在则创建):

```python
def test_interp_worker_has_app_interp_worker_logger():
    import app.modules.interpretation.worker as mod
    assert mod._log.name == "app.interp.worker"


def test_interp_worker_start_worker_calls_setup_logging(monkeypatch):
    import app.modules.interpretation.worker as mod
    calls = {"n": 0}
    monkeypatch.setattr("app.core.logging_config.setup_logging",
                        lambda d="INFO": calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(mod.rabbitmq, "consume", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(mod.rabbitmq, "start_consuming", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda *_: None)
    try:
        mod.start_worker()
    except RuntimeError:
        pass
    assert calls["n"] >= 1
```

- [ ] **Step 3.10: Run tests — verify they fail**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/test_interp_worker_bulk.py -v 2>&1 | tail -20`
Expected: FAIL

- [ ] **Step 3.11: Edit `interpretation/worker.py` — add logger + setup_logging**

File: `backend/app/modules/interpretation/worker.py`

Replace lines 1-9(import 区):

```python
import json
import logging

from app.core.database import get_hospital_db
from app.core.logging_config import setup_logging
from app.core.rabbitmq import rabbitmq, _NackOnce
from app.core.retry import backoff_for_retry, is_bulk_window_now
from app.ai.agents import run_interpretation_agent
from app.modules.report.batch_service import BatchService

_log = logging.getLogger("app.interp.worker")
```

Replace `start_worker()`(line 95-106)加 `setup_logging()`:

```python
def start_worker():
    setup_logging()
    while True:
        try:
            rabbitmq.consume("interpretation.urgent", handle_interpretation_task)
            rabbitmq.consume("interpretation.normal", handle_interpretation_task)
            rabbitmq.consume("interpretation.bulk", handle_interpretation_task)
            print("Interpretation worker started (urgent+normal+bulk)")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Worker disconnected: {e}, reconnecting in 3s...")
            import time
            time.sleep(3)
```

- [ ] **Step 3.12: Run tests — verify they pass**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/test_interp_worker_bulk.py -v 2>&1 | tail -20`
Expected: PASS

- [ ] **Step 3.13: Run full regression**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest -x -q 2>&1 | tail -10`
Expected: 全 PASS

- [ ] **Step 3.14: Commit**

```bash
cd /data/project/hospitalKnowledgeBase
git add backend/app/modules/report/extract_worker.py \
        backend/app/modules/report/worker.py \
        backend/app/modules/interpretation/worker.py \
        backend/tests/test_extract_worker.py \
        backend/tests/test_report_worker_bulk.py \
        backend/tests/test_interp_worker_bulk.py
git commit -m "feat(logging): wire setup_logging into 3 workers; rename important-step loggers"
```

---

### Task 4: 重塑 `interp_graph` / `judge_graph` / `chat_planner` / `batch_sweeper` 的 logger 名

**Files:**
- Modify: `backend/app/ai/agents/interp_graph.py:26`
- Modify: `backend/app/ai/agents/judge_graph.py:25`
- Modify: `backend/app/ai/agents/chat_planner.py:29`
- Modify: `backend/app/core/batch_sweeper.py:11`
- Test: `backend/tests/ai/test_agent_loggers.py`](新建)

**Interfaces:**
- Consumes: 无新依赖
- Produces: 4 个文件统一对应 `app.interp` / `app.judge` / `app.planner` / `app.batch.sweeper` logger

- [ ] **Step 4.1: Write failing test for logger names**

Create `backend/tests/ai/test_agent_loggers.py`:

```python
def test_interp_graph_logger_is_app_interp():
    from app.ai.agents import interp_graph
    assert interp_graph.logger.name == "app.interp"


def test_judge_graph_logger_is_app_judge():
    from app.ai.agents import judge_graph
    assert judge_graph.logger.name == "app.judge"


def test_chat_planner_logger_is_app_planner():
    from app.ai.agents import chat_planner
    assert chat_planner.logger.name == "app.planner"


def test_batch_sweeper_logger_is_app_batch_sweeper():
    from app.core import batch_sweeper
    assert batch_sweeper.log.name == "app.batch.sweeper"
```

- [ ] **Step 4.2: Run test — verify it fails**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/ai/test_agent_loggers.py -v 2>&1 | tail -20`
Expected: 4 个 test FAIL(当前 logger 名是 `__name__` / `"batch_sweeper"`)

- [ ] **Step 4.3: Rename loggers**

`backend/app/ai/agents/interp_graph.py` line 26:

```python
logger = logging.getLogger("app.interp")
```

`backend/app/ai/agents/judge_graph.py` line 25:

```python
logger = logging.getLogger("app.judge")
```

`backend/app/ai/agents/chat_planner.py` line 29:

```python
logger = logging.getLogger("app.planner")
```

`backend/app/core/batch_sweeper.py` line 11:

```python
log = logging.getLogger("app.batch.sweeper")
```

- [ ] **Step 4.4: Run test — verify it passes**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/ai/test_agent_loggers.py -v 2>&1 | tail -20`
Expected: 4 tests PASS

- [ ] **Step 4.5: Run regression on touched modules**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/ai tests/test_batch_sweeper.py tests/test_extract_worker.py -v 2>&1 | tail -20`
Expected: 全 PASS

- [ ] **Step 4.6: Commit**

```bash
cd /data/project/hospitalKnowledgeBase
git add backend/app/ai/agents/interp_graph.py \
        backend/app/ai/agents/judge_graph.py \
        backend/app/ai/agents/chat_planner.py \
        backend/app/core/batch_sweeper.py \
        backend/tests/ai/test_agent_loggers.py
git commit -m "feat(logging): rename agent loggers to app.interp/judge/planner + app.batch.sweeper"
```

---

### Task 5: `Settings.LOG_LEVEL` 字段 + `.env.example`

**Files:**
- Modify: `backend/app/config.py` (在某 FIELD 后插入)
- Modify: `.env.example`(若不存在则创建)

**Interfaces:**
- `Settings.LOG_LEVEL: str = "INFO"` — 仅文档/自省;`setup_logging()` **不读它**,以避免与 Settings 实例化的循环依赖

- [ ] **Step 5.1: Inspect** `backend/app/config.py` (已经 Task 0 探查过:line 7-8 是 APP_NAME/DEBUG,末尾 line 125 是 `model_config = {"env_file": ".env", "extra": "ignore"}`) 和 `.env.example`

Run: `cd /data/project/hospitalKnowledgeBase && ls -la backend/.env* .env* 2>&1; head -10 backend/.env.example 2>&1 || echo "no .env.example"`
Expected: 查看 `.env.example` 是否存在;若不存在则本 step 创建

- [ ] **Step 5.2: Add `LOG_LEVEL` to `Settings`**

File: `backend/app/config.py`

在 line 8(`DEBUG: bool = False`)下面插入:

```python
    # Logging
    LOG_LEVEL: str = "INFO"  # 控制日志级别;setup_logging() 优先读环境变量 LOG_LEVEL
```

- [ ] **Step 5.3: Add `LOG_LEVEL` to `.env.example`**

If `.env.example` exists, append one line at the end:

```
LOG_LEVEL=INFO
```

If not, create `.env.example`:

```
# 复制本文件为 .env 后填入真实值
LOG_LEVEL=INFO
```

不在仓库根目录的话:把目标定为仓库根目录的 `.env.example`(与 backend 分离的 common env)。若仓库根没有 `.env.example`,则同样创建在根。

- [ ] **Step 5.4: Run config tests — verify nothing broke**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest tests/test_config_batch.py -v 2>&1 | tail -10`
Expected: PASS

- [ ] **Step 5.5: Commit**

```bash
cd /data/project/hospitalKnowledgeBase
git add backend/app/config.py .env.example
git commit -m "feat(config): add LOG_LEVEL field + document in .env.example"
```

---

### Task 6: `start.sh` 改写到 `/data/logs/`

**Files:**
- Modify: `start.sh` (行精确替换,不改其它任何逻辑)

**Interfaces:**
- Consumes: 无
- Produces: 8 处 `nohup` 重定向目标由 `/tmp/*.log` 改为 `/data/logs/*.stdout.log`,并 `export LOG_LEVEL`

替换对照表:

| 旧路径 | 新路径 |
|-------|-------|
| `/tmp/vllm-medgo.log` | `/data/logs/vllm-medgo.stdout.log` |
| `/tmp/vllm-embed.log` | `/data/logs/vllm-embed.stdout.log` |
| `/tmp/reranker.log` | `/data/logs/reranker.stdout.log` |
| `/tmp/paddle-ocr.log` | `/data/logs/paddle-ocr.stdout.log` |
| `/tmp/backend.log` | `/data/logs/backend.stdout.log` |
| `/tmp/worker-parsing.log` | `/data/logs/worker-parsing.stdout.log` |
| `/tmp/worker-interpretation.log` | `/data/logs/worker-interpretation.stdout.log` |
| `/tmp/worker-extract.log` | `/data/logs/worker-extract.stdout.log` |

PID 文件(`.pid`)保持原样在 `/tmp/`,与日志解耦(便于 pkill 区分)。

- [ ] **Step 6.1: Add `mkdir -p /data/logs` + `export LOG_LEVEL` near the top**

Edit `start.sh` — 在 line 25 (`export PATH="$VENV:$PATH"`) 下面插入:

```bash
export LOG_LEVEL=${LOG_LEVEL:-INFO}
mkdir -p /data/logs
```

- [ ] **Step 6.2: Replace 8 redirect targets (sed-free, explicit edits)**

Run each Edit with the exact old/new pair from the table above. 一共 16 处 Edit(每行 `nohup ... > /xxx.log 2>&1 &` 一处,加上下面 `log "  ... (log: /xxx.log)"` 一处,共 16 处)。

每处独立 Edit,避免 `replaceAll` 误伤(同名 substring 在多个 echo 里出现)。

具体:
1. line 164 `> /tmp/vllm-medgo.log` → `> /data/logs/vllm-medgo.stdout.log`
2. line 166 `(log: /tmp/vllm-medgo.log)` → `(log: /data/logs/vllm-medgo.stdout.log)`
3. line 178 `> /tmp/vllm-embed.log` → `> /data/logs/vllm-embed.stdout.log`
4. line 180 `(log: /tmp/vllm-embed.log)` → `(log: /data/logs/vllm-embed.stdout.log)`
5. line 192 `> /tmp/reranker.log` → `> /data/logs/reranker.stdout.log`
6. line 194 `(log: /tmp/reranker.log)` → `(log: /data/logs/reranker.stdout.log)`
7. line 206 `> /tmp/paddle-ocr.log` → `> /data/logs/paddle-ocr.stdout.log`
8. line 208 `(log: /tmp/paddle-ocr.log)` → `(log: /data/logs/paddle-ocr.stdout.log)`
9. line 230 (`/tmp/vllm-*.log`) → `/data/logs/vllm-*.stdout.log`
10. line 240 (`/tmp/paddle-ocr.log`) → `/data/logs/paddle-ocr.stdout.log`
11. line 252 `> /tmp/backend.log` → `> /data/logs/backend.stdout.log`
12. line 254 下 (`/tmp/start-sh-backend.pid` 不动)
13. line 256 `(log: /tmp/backend.log)` → `(log: /data/logs/backend.stdout.log)`
14. line 262 `(检查 /tmp/backend.log)` → `(检查 /data/logs/backend.stdout.log)`
15. line 273 `> /tmp/worker-parsing.log` → `> /data/logs/worker-parsing.stdout.log`
16. line 276 `(log: /tmp/worker-parsing.log)` → `(log: /data/logs/worker-parsing.stdout.log)`
17. line 284 `> /tmp/worker-interpretation.log` → `> /data/logs/worker-interpretation.stdout.log`
18. line 287 `(log: /tmp/worker-interpretation.log)` → `(log: /data/logs/worker-interpretation.stdout.log)`
19. line 295 `> /tmp/worker-extract.log` → `> /data/logs/worker-extract.stdout.log`
20. line 298 `(log: /tmp/worker-extract.log)` → `(log: /data/logs/worker-extract.stdout.log)`
21. line 316-319 echo 汇总四行:把 `(log: /tmp/*.log)` 进一步改对(同 1/3/5/7)

> 同 substring `/tmp/vllm-medgo.log` 在 line 164 / 166 / 316 / cleanup() 可能出现多次。务必带周围 1-2 行 context 以唯一定位,不要 `replaceAll`。

- [ ] **Step 6.3: Lint check — bash syntax**

Run: `cd /data/project/hospitalKnowledgeBase && bash -n start.sh && echo "OK"`
Expected: `OK`

- [ ] **Step 6.4: Verify no stray `/tmp/*.log` in start.sh**

Run: `cd /data/project/hospitalKnowledgeBase && grep -n "/tmp/.*\.log" start.sh || echo "no matches"`
Expected: `no matches`

- [ ] **Step 6.5: Smoke test — `--help` style dry run is not feasible; run with `--no-models`**

Run: `cd /data/project/hospitalKnowledgeBase && bash start.sh --no-models 2>&1 | tail -30`
Expected:
- 显示「后端 API 已运行」或「启动后端 API (8000)」
- 显示「报告解析 Worker 已启动 (log: /data/logs/worker-parsing.stdout.log)」(确认含新路径)
- 不出 syntax error

然后停止新建进程并 verify `/data/logs/app.log` 创建:`ls -la /data/logs/`

- [ ] **Step 6.6: Stop services started by smoke test**

Run: `cd /data/project/hospitalKnowledgeBase && for p in backend parsing interpretation extract; do pidfile=/tmp/start-sh-worker-${p}.pid; [ "$p" = "backend" ] && pidfile=/tmp/start-sh-backend.pid; [ -f "$pidfile" ] && kill $(cat $pidfile) 2>/dev/null || true; done; pkill -f "uvicorn app.main:app" 2>/dev/null; pkill -f "app.modules.*.worker import start_worker" 2>/dev/null; true`

- [ ] **Step 6.7: Commit**

```bash
cd /data/project/hospitalKnowledgeBase
git add start.sh
git commit -m "chore(start.sh): redirect all stdout to /data/logs/*.stdout.log; export LOG_LEVEL"
```

---

### Task 7: AGENTS.md 增补「日志收口」节

**Files:**
- Modify: `AGENTS.md`(末尾追加章节)

**Interfaces:**
- Consumes: 实施完成的事实
- Produces: 后续 Agent 工程记忆

- [ ] **Step 7.1: Append the section**

Append to `AGENTS.md`:

```markdown
---

## 日志收口(2026-07-18 起)

**完整设计**: `docs/superpowers/specs/2026-07-18-logging-consolidation-design.md`

### 写入路径与轮转

- 所有 Python `logging` 调用收口到 **`/data/logs/app.log`**
- 按月初切分:旧月 rename 为 **`app.log.<YYYY-MM>``,`backupCount=0` 永久保留**(运维人工清理)
- 进程 stdout(via `start.sh` `nohup ... > /data/logs/<svc>.stdout.log 2>&1 &`):vllm-medgo / vllm-embed / reranker / paddle-ocr / backend / worker-parsing / worker-interpretation / worker-extract
- 配置入口: `backend/app/core/logging_config.py::setup_logging()`,纯 stdlib,无三方依赖

### 重要 logger 命名表(引用请用这些名字)

| logger name | 用途 |
|------|------|
| `app.parse` | 报告解析(report/worker.py 预留,现仍用 print) |
| `app.upload` | 上传(batch_router,当前未加 logger) |
| `app.interp` | LLM 解读(interp_graph.py) |
| `app.interp.worker` | 解读 worker(interpretation/worker.py 预留,现仍用 print) |
| `app.judge` | judge_graph.py |
| `app.planner` | chat_planner.py |
| `app.batch` | batch_router/batch_service(预留) |
| `app.batch.sweeper` | batch_sweeper.py + main.py 启动回调 |
| `app.batch.extract` | extract_worker.py(批量解压) |
| `app` | 全局异常 handler |

其余模块保持 `__name__` logger,retriever / kg_* / citation_matcher / term_normalizer / redis / tenant / user_profile 等均由 root handler 统一捕获写入 `app.log`,无需改动。

### LOG_LEVEL 环境变量

- `start.sh` 顶部 `export LOG_LEVEL=${LOG_LEVEL:-INFO}`,所有子进程继承
- `setup_logging()` 优先读 `os.environ["LOG_LEVEL"]`;`Settings.LOG_LEVEL` 字段仅作文档,不被 setup_logging 消费(避免循环依赖)
- 调级别示例: `LOG_LEVEL=DEBUG bash start.sh --no-models`

### Worker `print()` 双轨说明

per spec 决策:workers (report/worker.py / interpretation/worker.py / extract_worker.py) 现有 `print()` **不强迁**到 logging。结果:
- `print()` → 经 `nohup > /data/logs/worker-*.stdout.log` 落 stdout 文件
- 新加的 `logging.getLogger("app.parse")` / `app.interp.worker` / `app.batch.extract` 已就位,未来新增 `logger.info(...)` 会自动写入 `/data/logs/app.log`
- 排查 worker 时需同时看 `app.log`(logging)与 `worker-*.stdout.log`(print),双轨并存直到全量迁移完成

### 多进程边界提示

`MonthlyRotatingFileHandler` 不加文件锁。月初同时由多个 worker 进程触发 `doRollover()` 的极小概率会让当月文件被 rename 两次,导致约一条日志重写。月切本身就极低频,不引入第三方库的代价换来的这一边角可接受。若日后需要严格进程安全,再单独评估 `concurrent-log-handler` 在 cu126 主 venv 内的兼容性。
```

- [ ] **Step 7.2: Commit**

```bash
cd /data/project/hospitalKnowledgeBase
git add AGENTS.md
git commit -m "docs(agents): document logging consolidation (path / loggers / LOG_LEVEL / print double-track)"
```

---

### Task 8: 全套验证 + end-to-end smoke

**Files:**
- 无新文件

- [ ] **Step 8.1: Run full test suite**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ./.venv/bin/pytest -q 2>&1 | tail -15`
Expected: 全 PASS(原本 186 + 本计划新增 ~10 个)

- [ ] **Step 8.2: Run linters if configured**

Run: `cd /data/project/hospitalKnowledgeBase/backend && ls pyproject.toml && grep -E "ruff|black|mypy" pyproject.toml 2>&1 | head; ./.venv/bin/python -c "import app.core.logging_config; import app.main; print('imports OK')"`
Expected: 无 linter 配置则只做 import smoke;`imports OK`

- [ ] **Step 8.3: Start services + tail log**

Run: `cd /data/project/hospitalKnowledgeBase && bash start.sh --no-models 2>&1 | tail -25`
Expected: 后端 + 3 个 worker 启动,且 `/data/logs/app.log` 文件被创建并开始有内容,`/data/logs/worker-*.stdout.log`、`/data/logs/backend.stdout.log` 同时被写入

- [ ] **Step 8.4: Verify log content & logger names**

Run: `sleep 5 && grep -E "INFO:app\.|WARNING:app\.|INFO:uvicorn" /data/logs/app.log | head -20`
Expected: 看到 logger 名含 `app.batch.sweeper` / `uvicorn` / `app.interp` 等(若有 interp/judge 调用就触发)

- [ ] **Step 8.5: Verify logging level via env var**

Run: `cd /data/project/hospitalKnowledgeBase && pkill -f "uvicorn app.main" 2>/dev/null; pkill -f "start_worker\|app.modules.*worker" 2>/dev/null; sleep 2; LOG_LEVEL=DEBUG bash start.sh --no-models 2>&1 | tail -5; sleep 5; grep -c "DEBUG:" /data/logs/app.log`
Expected: DEBUG 行数 > 0(说明 LOG_LEVEL 环境变量生效)

- [ ] **Step 8.6: Stop all services started in verification**

Run: `cd /data/project/hospitalKnowledgeBase && for pidfile in /tmp/start-sh-*.pid; do [ -f "$pidfile" ] && kill $(cat $pidfile) 2>/dev/null || true; rm -f "$pidfile"; done; pkill -f "uvicorn app.main:app" 2>/dev/null; pkill -f "from app.modules.*worker import start_worker" 2>/dev/null; true`

- [ ] **Step 8.7: Verify `start.sh` references all updated**

Run: `cd /data/project/hospitalKnowledgeBase && grep -c "/data/logs/" start.sh && grep -c "/tmp/.*\.log" start.sh`
Expected: 第一个数 ≥ 8,第二个数 = 0

- [ ] **Step 8.8: Final commit if any leftover changes**

Run: `cd /data/project/hospitalKnowledgeBase && git status -s && git diff --stat`
如果有 uncommitted 改动(比如 smoke test 产生的 lock 文件之外的真实改动),补一个 commit;否则跳过

---

## Self-Review 记录

- **Spec 覆盖**: §4.1 路径 → Task 1+6;§4.2 handler → Task 1;§4.3 setup_logging → Task 1;§4.4 入口 → Task 2+3;§4.5 logger 命名表 → Task 3+4(注:`app.upload` 按决策不强加 logger,只写进 AGENTS.md);§4.6 start.sh → Task 6;§4.7 Settings/.env.example → Task 5;§4.8 AGENTS.md → Task 7;§6 错误处理(setup_logging 回退 stdout)→ Task 1.8;§7 测试(月轮转 + LOG_LEVEL + 手动验证)→ Task 1.x + Task 8
- **Placeholder scan**: 无 TBD / TODO;所有 code block 给的是最终代码(注:Task 1.3→1.4 故意一次性写完 ShouldRollover 后再修正,两 step 合并为可读)
- **Type consistency**: `setup_logging(default_level: str = "INFO") -> None` 在 Task 1 与所有 worker/main 调用点一致;`MonthlyRotatingFileHandler(filename: str, **kwargs)` 在 Task 1 自产自销
- **命名一致性**: `app.batch.extract` / `app.batch.sweeper` / `app.interp` / `app.interp.worker` / `app.judge` / `app.planner` / `app.parse` 全程一致