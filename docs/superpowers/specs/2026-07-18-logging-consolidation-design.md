# 日志收口设计 — `/data/logs` 月轮转聚合

**日期**: 2026-07-18
**状态**: 设计稿
**作者**: brainstorming skill

---

## 1. 背景与现状

项目当前**没有集中化的日志管理**：

- 没有任何 `logging.basicConfig` / `dictConfig` / 配置文件，所有 logger 走 Python 默认格式 (`LEVEL:logger_name:message`)
- 16 个文件各自 `logging.getLogger(__name__)` 或起显式名字（`"batch_sweeper"`、`"extract_worker"`、`"tenant"` 等），命名不统一
- 部分关键 worker (`report/worker.py`、`interpretation/worker.py`、`extract_worker.py`) 使用 `print()` 而非 logging，与其它模块风格不一致
- `start.sh` 用 `nohup ... > /tmp/*.log 2>&1 &` 重定向 8 个进程的 stdout，无轮转、无保留、无限增长
- 没有日志级别可调（无 `LOG_LEVEL` 环境变量或 config 字段）
- 没有时间戳（Python 默认格式不含），跨月历史日志无法定位时间

## 2. 目标

1. 把所有 Python 日志收口到 `/data/logs/app.log`，按月轮转
2. 旧月日志以 `app.log.<YYYY-MM>` 命名，永久保留（运维人工清理）
3. 重要业务步骤的 logger 名统一，便于按步骤 grep 排查
4. 日志级别可由环境变量 `LOG_LEVEL` 调节，不再需改代码
5. `start.sh` 的 `nohup` 重定向目标也归集到 `/data/logs/`，统一运维入口
6. workers 的 `print()` 不强迁，仅改 shell 管道目标，与"统一收口到 /data/logs"语义一致

## 3. 非目标

- 不引入结构化日志（JSON / correlation ID / 中间件）—— 未来如需要再单独立项
- 不重构业务日志消息内容
- 不改 workers 现有的 `print()` 语句
- 不引入第三方日志库（保持纯 stdlib）

## 4. 架构

### 4.1 日志写入路径

```
/data/logs/
├── app.log                  # 本月 Python logging 聚合文件
├── app.log.2026-06          # 历史月（自动生成，永久保留）
├── app.log.2026-07
├── backend.stdout.log       # FastAPI stdout 落盘（来自 nohup）
├── worker-parsing.stdout.log
├── worker-interpretation.stdout.log
├── worker-extract.stdout.log
├── vllm-medgo.stdout.log
├── vllm-embed.stdout.log
├── reranker.stdout.log
└── paddle-ocr.stdout.log
```

**说明**: 一份 Python 日志聚合文件 `app.log` 承载所有 `logging` 调用（含 uvicorn access / error logger）；各进程的 stdout 走 nohup 重定向单独落盘，便于区分进程异常输出与 Python logging 内容。

### 4.2 月轮转 Handler

新增 `backend/app/core/logging_config.py`，提供自定义 `MonthlyRotatingFileHandler`：

```python
class MonthlyRotatingFileHandler(TimedRotatingFileHandler):
    """按月初切分，suffix="%Y-%m"，无需 backupCount（永久保留）"""
    def __init__(self, filename, ...):
        super().__init__(
            filename=filename,
            when="MIDNIGHT",      # 占位，实际由 shouldRollover 决定
            interval=1,
            backupCount=0,        # 0 = 永久保留
            encoding="utf-8",
            utc=False,
        )
```

关键行为：
- `shouldRollover(record)`: 比较 `record.time` 与当前开关时间所在月的下一个月初 1 号 0:00；超过则触发
- `doRollover()`: rename 当前 `app.log` → `app.log.<上个月 YYYY-MM>`，再新建空 `app.log`
- `backupCount=0`: 不删除任何历史文件
- 多进程边界的极小双 rename 概率可接受（月切本身就低频）

Formatter:
```python
fmt = "%(asctime)s | %(levelname)s:%(name)s:%(message)s"
datefmt = "%Y-%m-%d %H:%M:%S"
```

输出示例：
```
2026-07-18 12:34:56 | INFO:app.parse:report_task=rt-001 start parsing
2026-07-18 12:35:01 | WARNING:app.interp:LLM retry 1/3 due to timeout
2026-07-18 12:40:33 | ERROR:app.batch.sweeper:sweep crashed
```

### 4.3 配置入口

`backend/app/core/logging_config.py` 导出：

```python
def setup_logging(default_level: str = "INFO") -> None:
    """
    在每个 Python 进程入口调用一次。
    优先读 os.environ["LOG_LEVEL"]，否则用 default_level。
    会创建 /data/logs/ 目录（mode=0o775）。
    """
```

行为：
- `level = os.environ.get("LOG_LEVEL", default_level).upper()`
- `os.makedirs("/data/logs", mode=0o775, exist_ok=True)`
- `handler = MonthlyRotatingFileHandler("/data/logs/app.log")`
- `handler.setFormatter(Formatter(fmt, datefmt))`
- `root = logging.getLogger(); root.handlers.clear(); root.addHandler(handler); root.setLevel(level)`
- `logging.getLogger("uvicorn")` / `"uvicorn.access"` 不设独立 handler，让其冒泡到 root
- 不调 `dictConfig`，保持显式简单

### 4.4 入口调用点

`start.sh` 通过 `nohup $VENV/python -c "from app.modules.xxx.worker import start_worker; start_worker()"` 拉起 worker，即每个 worker 模块暴露一个 `start_worker()` 函数（**不是** `if __name__ == "__main__":` 块）。因此 `setup_logging()` 要在 `start_worker()` 函数第一行调用，紧贴在 import 业务模块之后、业务循环开始之前。

| 进程 | 入口位置 | 调用位置 |
|------|---------|---------|
| FastAPI backend | `backend/app/main.py::create_app` | 函数第一行 |
| Report parsing worker | `backend/app/modules/report/worker.py::start_worker` | 函数第一行 |
| Interpretation worker | `backend/app/modules/interpretation/worker.py::start_worker` | 函数第一行 |
| Batch extract worker | `backend/app/modules/report/extract_worker.py::start_worker` | 函数第一行 |

**说明**: 这几个 worker 是 `start.sh` 用 `nohup ... python -c "from ... import start_worker; start_worker()"` 起的独立 Python 进程，与 FastAPI 主进程不共享 logger 状态，因此每个都需独立调一次 `setup_logging()`。

### 4.5 Logger 命名收口

| 业务步骤 | 新 logger 名 | 当前来源 |
|---------|-------------|---------|
| 解析 / 批量解压 parsing | `app.parse` | `"extract_worker"` (`extract_worker.py`)。注：`start.sh` 命名 extract_worker 日志为「批量解压 Worker」，语义上稍偏 batch；但本设计按"已有 logger 改名"原则统一为 `app.parse`，实施时可酌情改为 `app.batch.extract`。**最终名称以实施计划为准。** |
| 上传 upload | `app.upload` | batch_import 上传相关 router/service 若已有 `__name__` 或 `"extract_worker"` 等命名则统一改名；若仅用 `print()` 则按"print() 不变"原则不强迁，仅在未来新加日志时使用此名。 |
| LLM 解读 | `app.interp` | `__name__` (`interp_graph.py`) |
| judge | `app.judge` | `__name__` (`judge_graph.py`) |
| planner | `app.planner` | `__name__` (`chat_planner.py`) |
| 批量上传 / sweeper | `app.batch` / `app.batch.sweeper` | `"batch_sweeper"` (`batch_sweeper.py` + `main.py` 中的 sweeper 后台任务)；batch_router/batch_service 中已有 logger 同样改为 `app.batch` |

**未列入"重要步骤"的模块**保持现状 `__name__`（retriever / kg_client / kg_retriever / citation_matcher / chat_graph / term_normalizer / redis / tenant / user_profile / ...）。它们的 logger 名本身已具备可读性，root handler 会统一捕获并写入同一份 `app.log`，无需改动。

**print() 处理原则**：按已确认非目标，workers 现有 `print()` 不强迁到 logging。对于"重要步骤"中当前仅有 `print()` 的入口（如 `report/worker.py` 的报告解析 worker、`interpretation/worker.py`），仅在该 worker 的 `start_worker()` 顶部加 `setup_logging()` 调用以使其未来产生的 logging 能写入；现有 `print()` 继续走 stdout 落到 `/data/logs/worker-*.stdout.log`，与 `/data/logs/app.log` 构成双轨，AGENTS.md 中明确说明。

### 4.6 start.sh 调整

- 在脚本顶部初始化区加 `mkdir -p /data/logs`
- 在 `export` 段加 `export LOG_LEVEL=${LOG_LEVEL:-INFO}`，让所有子进程继承
- 把 8 处 `nohup ... > /tmp/xxx.log 2>&1 &` 的目标改为 `/data/logs/xxx.stdout.log`
  - 不变量：命令本身、参数、`&` 后台执行
  - 变量：仅重定向目标文件名

### 4.7 配置项文档化

- `backend/app/config.py::Settings` 加 `LOG_LEVEL: str = "INFO"` 字段（仅用作 Settings 自省与 OpenAPI 文档，不作为 setup_logging 的数据源——setup_logging 仍优先读 `os.environ` 避免循环依赖 Settings 实例化）
- `.env.example` 追加 `LOG_LEVEL=INFO` 一行

### 4.8 AGENTS.md 增补

在 AGENTS.md 末尾追加一节「日志收口」：
- 写入路径：`/data/logs/app.log` + 月轮转 + `app.log.<YYYY-MM>` 永久保留
- 重要 logger 命名表（app.parse / app.upload / app.interp / app.judge / app.planner / app.batch / app.batch.sweeper）
- `LOG_LEVEL` 环境变量说明
- worker `print()` 仍存在且走 nohup 重定向到 `/data/logs/worker-*.stdout.log`（双轨），后续如需迁到 logging 再单独立项
- 主 venv / vLLM venv / paddle venv 各自进程都调用 `setup_logging()` 的位置说明
- 多进程轮转边界说明（小概率双 rename 可接受）

## 5. 涉及文件

| 文件 | 改动类型 |
|------|---------|
| `backend/app/core/logging_config.py` | 新建 |
| `backend/app/main.py` | 调 `setup_logging()`；sweeper 启动回调 logger 名改 `app.batch.sweeper` |
| `backend/app/modules/report/extract_worker.py` | 在 `start_worker()` 第一行调 `setup_logging()`；logger 名由 `"extract_worker"` 改为 `app.parse`（或实施时定为 `app.batch.extract`） |
| `backend/app/modules/report/worker.py` | 在 `start_worker()` 第一行调 `setup_logging()`；`print()` 不动 |
| `backend/app/modules/interpretation/worker.py` | 在 `start_worker()` 第一行调 `setup_logging()`；`print()` 不动 |
| `backend/app/ai/agents/interp_graph.py` | logger 名由 `__name__` 改为 `app.interp` |
| `backend/app/ai/agents/judge_graph.py` | logger 名由 `__name__` 改为 `app.judge` |
| `backend/app/ai/agents/chat_planner.py` | logger 名由 `__name__` 改为 `app.planner` |
| `backend/app/core/batch_sweeper.py` | logger 名由 `"batch_sweeper"` 改为 `app.batch.sweeper` |
| batch_import 上传相关 router/service（实施时定位具体文件） | 若已有 logger 改为 `app.upload` / `app.batch`；若无不强加 |
| `backend/app/config.py` | 加 `LOG_LEVEL` 字段 |
| `.env.example` | 加 `LOG_LEVEL=INFO` |
| `start.sh` | mkdir /data/logs、export LOG_LEVEL、8 处重定向目标从 `/tmp/*.log` 改为 `/data/logs/*.stdout.log` |
| `AGENTS.md` | 追加「日志收口」节 |

## 6. 错误处理

- `setup_logging()` 内 catch `PermissionError` / `OSError`：回退到 stdout StreamHandler 并 `print` 一条告警，不让进程因为日志初始化失败而崩
- `MonthlyRotatingFileHandler.doRollover()` 内若 rename 失败（如目标已存在非预期），记录一条 `WARNING` 到当前 handler 然后继续 append（不丢日志，但可能该月没切分；下次月切再重试）

## 7. 测试

- 单元测试：`MonthlyRotatingFileHandler` 用 `freezegun` 模拟月初过渡，断言 `app.log.YYYY-MM` 生成、新 `app.log` 为空、内容不丢失
- 单元测试：`setup_logging()` 在 `LOG_LEVEL=DEBUG` / `WARNING` 下 root level 正确生效
- 手动验证：`bash start.sh` 起来后 `tail -f /data/logs/app.log` 能看到 backend、worker 都在写，并能看到 `app.parse` / `app.interp` 等 logger 名
- 手动验证：跨月模拟切换（改系统时间或 freezegun），确认轮转文件生成

## 8. 风险与取舍

| 风险 | 评估 | 处理 |
|------|------|------|
| 多进程同时触发 rename 造成双 rename | 月切极低频，可接受 | 不引入第三方库；文档说明 |
| 永久保留导致磁盘占用持续增长 | 用户明确选择永久保留 | 文档告知运维人工清理；后续如要限制再调 `backupCount` |
| `print()` 仍走 stdout 重定向，与 logging 双轨 | 已在非目标中明确不迁 | 文档说明双轨现状 |
| vLLM / paddle 子进程的 stdout 改名后，历史 `/tmp/*.log` 不再写 | 用户期望收口到 `/data/logs`，与目标一致 | 历史文件可由运维删除 |

## 9. 与 AGENTS.md 既有约定的兼容性

- 不改 `backend/pyproject.toml` 依赖（纯 stdlib）
- 不动 vLLM venv / paddle venv 内部
- `start.sh` 改的是行内字符串，不破坏 GPU 分配逻辑
- 不影响 start.sh 的 DB DDL 初始化块
- 不影响 3 个 venv 的隔离关系