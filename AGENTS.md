# AGENTS.md —— 给后续 AI Agent 的工程记忆

本文件记录代码里不容易直接看出的工程决策与环境约束,供后续接手的 Agent 快速理解。**修改本文件前请确认事实,不要凭推测改写。**

---

## vLLM 不在 `backend/pyproject.toml` 的依赖里 (重要)

**事实**: `start.sh` 用 `backend/.venv-vllm-cu12/bin/vllm` 启动 MedGo / BGE-M3 两个推理服务,该 venv 是**手工独立维护**的,**不进 `uv.lock`**。

**原因**:
- vLLM 在本架构里是 `start.sh` 拉起的外部 HTTP 服务,backend 业务代码 **没有任何 `import vllm`**(已核实 `app/`、`reranker_service/` 下均无)。
- 主 venv (`backend/.venv`) 因驱动 535 / CUDA 12.2 限制只能跑 cu12(torch 2.7+cu126);而 vLLM 在 backend 的 `requirements` 中默认会被 uv 解到 cu13 + vllm 0.23,与驱动不兼容,启动即崩。
- 所以把 vllm 从 backend 依赖里移除,改由独立 venv 提供;主 venv 仍用 cu12 跑 Backend / Reranker / Workers。

**不要做的事**:
- ❌ 不要往 `backend/pyproject.toml` 里重新加 `"vllm>=..."`
- ❌ 不要 `cd backend && uv sync` 期望它装出能跑 vllm 的环境 —— 它故意不装 vllm
- ❌ 不要把 `.venv-vllm-cu12/` 删了重建为最新版 vllm(0.22+)—— 那会拉到 cu13 / torch 2.11,与驱动 535 不兼容

**重建 `.venv-vllm-cu12` 的方法(如丢失)**:
```bash
cd backend
uv venv .venv-vllm-cu12 --python 3.10
UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv pip install \
  --python .venv-vllm-cu12/bin/python \
  'vllm==0.9.2' 'transformers==4.51.3' 'tokenizers==0.21.4'
```
注意 transformers 必须 pin 4.51.3 —— vllm 0.9.2 与 transformers 5.x 冲突(`aimv2` 注册重复 + `prepare_for_model` 被移除,FlagEmbedding 还在用)。

**核对 venv 健康**:
```bash
backend/.venv-vllm-cu12/bin/python -c "import torch,vllm;print(torch.__version__,vllm.__version__,torch.cuda.is_available())"
# 期望: 2.7.0+cu126 0.9.2 True
```

---

## 主 venv (`backend/.venv`) 的 cu12 锁定

`backend/pyproject.toml` 末尾有:
```toml
[tool.uv]
torch-backend = "cu126"
```
且 `dependencies` 中显式 pin:
- `torch==2.7.0` —— 否则 uv 会拉 torch 2.11+cu13,在本机驱动 535 上 import 即崩
- `transformers==4.51.3` —— 配合 FlagEmbedding 1.4(transformers 5.x 移除了 `prepare_for_model`)
- `tokenizers==0.21.4` —— transformers 4.51 配套

**改这些 pin 前请确认驱动支持**:当前 nvidia 驱动 535.247.01 / CUDA 12.2,Ubuntu 20.04 apt 源顶天到 575(无 580+),所以 cu13 路径在本机走不通。要升驱动只能先升 OS(不在本项目范围)。

---

## start.sh GPU 分配 (4×L20, 每卡 45GB)

| GPU | 服务 | 显存占用 |
|-----|------|---------|
| 0,1,2,3 | MedGo vLLM (TP=4, 32K ctx, util 0.6, enforce-eager) | ~27.8GB/卡 |
| 2 | BGE-M3 vLLM (util 0.12) + Reranker (主venv) | ~angoing |
| 3 | PaddleOCR-VL (paddle_venv, 独立) | 较小 |

`enforce-eager` 关闭 CUDA 图,降低显存碎片,利于 4 卡共存场景。`--gpu-memory-utilization 0.6` 给 MedGo 是为给同卡上的 BGE/Reranker/OCR 让出空间。

**3 个 venv 关系**:
- `backend/.venv` —— 主 FastAPI 后端 / Reranker / Workers (uv 管理, cu12)
- `backend/.venv-vllm-cu12` —— 仅供 start.sh 拉 MedGo/BGE (手工, vllm 0.9.2+cu126)
- `backend/paddle_venv` —— PaddleOCR-VL 专属 (uv 管理)

---

## 验证一切就绪

```bash
# 全套健康
for p in 8000 8004 8002 8003 8001; do curl -s -m2 http://localhost:$p/health >/dev/null && echo ":$p UP" || echo ":$p DOWN"; done

# MedGo 推理(TP=4)
curl -s http://localhost:8004/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"/data/models/MedGo","messages":[{"role":"user","content":"hello"}],"max_tokens":10}'

# BGE-M3 Embedding
curl -s http://localhost:8002/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"model":"BAAI/bge-m3","input":"test"}'

# Reranker
curl -s http://localhost:8003/rerank -H 'Content-Type: application/json' \
  -d '{"query":"糖尿病","documents":["血糖","天文"],"top_n":2}'
```

冷启动 `bash start.sh` 在 ~90 秒内能完成全部服务启动(已验证)。

---

## 备份 (按需清理)

- `backend/.venv.bak-cu13-*` —— 原 cu13 损坏态历史快照,可删
- `backend/.venv.bak-pre-cu12fix-*` —— cu12 化前快照,可删
- `backend/pyproject.toml.bak-*` / `backend/uv.lock.bak-*` —— 文本回滚备份,可删
- `/tmp/nvidia-snap-latest.txt` —— 驱动包列表只读快照,可删

git 已跟踪改动可直接 `git checkout -- start.sh backend/pyproject.toml backend/uv.lock` 回滚;`.venv-vllm-cu12` 是新目录不在 git 内,如需彻底回滚需手动删。

---

## 新 tenant 初始化必读

`start.sh` 的数据库初始化 DDL 块只对 `hospital_H001` 跑一次(`CREATE TABLE IF NOT EXISTS`)。**新增 tenant 时必须照此 DDL 块为新 tenant 的库完整执行一遍**,否则该 tenant 缺表会直接报错。完整表清单(逐表对应 `start.sh` 内的 `CREATE TABLE IF NOT EXISTS`):

| 旧业务表 | 用途 |
|------|------|
| `hospital_user` | 医院用户档案 |
| `knowledge_category` / `knowledge_entry` | 知识库分类与条目 |
| `report_task` / `report_info` / `report_indicator` | 体检报告解析 |
| `report_interpretation` / `indicator_judgment` | AI 解读与指标判定 |
| `triage_rule` | 分诊规则 |
| `report_template` / `statistic_cache` / `dispatch_config` / `resource_metric` | 模板/统计缓存/分诊配置/资源监控 |
| `chat_session` / `chat_message` | 聊天会话 |

| 批量上传新增表(易遗漏) | 用途 |
|------|------|
| `batch_import` | 批量上传批次 |
| `batch_import_file` | 批次内单文件(含 `failed_stage` 列,记录失败阶段;`dispatch_hospital` 列记录分发目标医院) |

`batch_import_file.failed_stage` 是增量列,旧库需 `ALTER TABLE batch_import_file ADD COLUMN IF NOT EXISTS failed_stage VARCHAR(24) DEFAULT NULL`(`start.sh` 已带,新 tenant 建表时直接包含)。

`batch_import_file.dispatch_hospital`(2026-09-03 起):文件名解析出的**目标医院**(跨院分发时 ≠ 批次 `hospital_id`),worker/`retry_failed` 据此定位任务所在库。单医院场景为 NULL。存量库迁移:`scripts/manual_migrations/005_add_dispatch_hospital.sql`(本机 MySQL 8 不支持 `ADD COLUMN IF NOT EXISTS`,用纯 ALTER)。

`failed_stage` 已知取值:`parsing` / `interpretation` / `oversize` / `dispatch_unmatched` / `hospital_not_found`。
- `oversize`:单文件 > 50MB,无 `report_task_id`,**不可重试**(UI 禁用重试按钮)。
- `dispatch_unmatched`:批量上传时文件名不符合 `<姓名>_<身份证后六位>.<ext>` 约定(两段下划线、末段 5 位数字 + 末位 0-9/X),不 create_task 不投 parsing。**不可重试**,需 admin 改文件名后整批重新上传。
- `hospital_not_found`:文件名格式合法,但外部接口(`EXTERNAL_RESOLVER_URL`,baUser searchUser)按 `realName+idCardLast6` 无精确匹配、解析出 orgId 本地未注册、或匹配歧义。**不可重试**。
- 后端 `retry_failed` 把这三类统称 unretryable,在响应里以 `skipped_unretryable` 计数返回,不重投。

## 批量上传跨院分发:进度与重试跨库定位(2026-09-03 起)

**事实**:批量上传时 `BatchImport`/`BatchImportFile`/进度计数器写在上传方(批次)库,而 `report_task`/`report`/解读跑在**文件名解析出的目标医院库**(可 ≠ 上传方)。若 worker 用目标库记批次进度,会 `file_not_found` 让批次永远卡 `parsing`(2026-09-03 真实故障)。

**修法**:
- 消息(`parsing`/`interpretation`)payload 带 `batch_hospital_id`(=批次库/上传方医院);worker 记进度用 `BatchService.update_batch_progress(batch_hospital_id, hospital_id, db, ...)`:两者一致走当前会话,不一致另开批次库会话。`service.create_task` / `service.process_task` 负责把该字段透传(extract_worker 从 `b.hospital_id` 取)。
- `BatchImportFile.dispatch_hospital` 存每文件的目标医院;`retry_failed` 重投/定位任务按它打开目标库,再带 `batch_hospital_id` 发布(旧数据 NULL → 回退 `b.hospital_id`,行为不变)。
- 测试:`tests/test_batch_cross_hospital.py`(update_batch_progress 同/跨库 + retry 跨库路由)、`tests/test_extract_worker.py::test_cross_hospital_dispatch_records_target_and_batch_hospital`。**改回仅用单库会话前先看这些测试**。


## 批量上传文件名约定(2026-09-01 起)

- 命名:`<姓名>_<身份证后六位>.<ext>`,后六位 = 5 位数字 + 末位数字或 X(校验位)。
- 用户锚定 = **姓名 + 后六位(双锚定)**:`report_info.name` / `chat_session.name` 存 `name`(姓名,VARCHAR(50)),各表存 `user_id`(后六位字符串,VARCHAR(16));`report_task` 只有 `user_id`(无 `name` 列)。报告列表/档案/chat 一律按 **`user_id == 后六位 AND name == 姓名`** 双条件匹配。
- **展示名与归属分离(2026-09-02)**:`report_info` 另有 `parsed_name` 列,存 PDF 解析出的**报告真实姓名**(仅展示)。`name` 存归属锚定名(批量=文件名姓名段;单份上传=登录账号锚定名 `current_user.name`,见 `report_router.upload_report`)。列表/详情的 `name` 字段返回 `parsed_name or name`(展示真实姓名),归属过滤仍按 `name` 双锚定不动。加字段/迁移需对**每个** tenant 库执行:`ALTER TABLE report_info ADD COLUMN parsed_name VARCHAR(50) NULL`(2026-09-02 已在 hospital_H001-H004/hospital_1 执行;新建 tenant 需在 DDL/存储过程补齐)。
- `platform_user.id_card_suffix` / `platform_user.name` 存登录用户双锚定,登录后经 JWT 带出(CurrentUser 有 `id_card_suffix` / `name` 字段)。
- 注册唯一性:`platform_user` 上 (hospital_id, name, id_card_suffix) 三元组唯一,`/auth/register` 对重复组合返回 400。
- `chat_session.name` 列:create_session 时落 `name`,list/get/delete/update 会话按双条件过滤。
- 存量数据(user_id 为旧数字 ID、name 为 NULL 的行)按「存量不动」原则:双条件匹配只对新会话/新报告/新用户生效。
- 外部接口:`EXTERNAL_RESOLVER_URL` 配置,契约见 `docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md §3`。
- 旧 `<姓名>_<医院编号>_<用户编号>` 三段命名已废弃;存量数据 user_id 仍为旧数字 ID,不迁移(只影响新数据)。
- 外部接口契约:`GET {EXTERNAL_RESOLVER_URL}?realName={姓名}&idCardLast6={后六位}` → baUser 信封
  `{code,msg,data}`;data 数组按 `realName==姓名 AND idCardLast6==后六位` 精确过滤,唯一命中项
  `str(orgId)` 即 hospital_id(orgId 与本地 hospital_tenant.hospital_id 一致)。

---

## 外部 App 免密登录(app-login)(2026-09-01 起)

**事实**: `backend/app/api/auth.py` 提供 `POST /api/v1/auth/app-login`,外部 App 用
`app_key + name + id_card_suffix` 换取与普通登录一致的 JWT(role='user',有效期
`APP_LOGIN_TOKEN_EXPIRE_MINUTES` 默认 7 天),再以 Bearer 调用现有 `/api/v1/reports/*`、
`/api/v1/chat/*`(router 零改动)。hospital_id 由 `resolve_hospital(name, id_card_suffix)` 经
`EXTERNAL_RESOLVER_URL` 解析。

**信任模型(重要)**: 持有 `APP_API_KEY` 的系统可代任意 `(name, 后六位)` 签发 user token,
等于可访问任意用户的报告与 chat。必须 TLS + key 保密,仅给可信 HIS。

**配置**(`backend/.env`):
```
APP_API_KEY=<全局密钥>              # 空 = app-login 一律 401
APP_LOGIN_TOKEN_EXPIRE_MINUTES=10080
EXTERNAL_RESOLVER_URL=http://...    # 未配置时 resolver 返回 None → 401
```

**行为约定**:
- `platform_user` 三元组 `(hospital_id, name, id_card_suffix)` 不存在时**自动注册**:
  username = `app_<hospital_id>_<name>_<id_card_suffix>`,password_hash 为随机串(不可密码登录)。
- 错误码:key 错误 / resolver 无匹配 → 401;name 空 / 后六位非法 → 400;resolver 宕机 → 503。
- app_key 用 `secrets.compare_digest` 常量时间比较。
- 存量 `platform_user` 必须先跑 `003_user_id_suffix.sql` 迁移,否则新列不存在会 500。

---

## 报告对比默认基线退化策略(2026-09-02 起)

**事实**: `backend/app/modules/user_profile/service.py::_auto_select_baseline` 现在**允许选任意其它报告**。
选择逻辑(该用户锚定 user_id 后六位 + name 内、排除当前报告):
1. 优先取 `report_date` **严格早于**当前报告且最接近的一份(原行为,保存「与上次报告对比」语义);
2. 若无更早(当前即该用户最早一份报告,如首页按 `created_at` 倒序把日期最早的报告排在最上)→ **退化**为该用户 `report_date` 与当前报告 `|日期差|` 最小的一份(不再返回 None);
3. 全部无 `report_date` → 取最近 `created_at` 的另一份;用户仅 1 份报告仍返回 None。

**原因**: 退化前返回 None 会让前端 `frontend/packages/user-portal/src/components/ComparisonCard.tsx`(`if (!data || !data.baseline) return null`)整卡不渲染,用户连「选择历史报告」下拉都看不到。现 UI 标题为「📊 与历史报告对比」;`GET /profile/compare?baseline_id=` 对任意属于该锚定的报告都放行(不限早于当前),AI 小结 `/profile/ai-summary` 同理。

**测试**: `backend/tests/user_profile/test_service.py` 新增 4 条(最早一份→退化到日期最近 / 有更早→仍取更早且最近 / 单份报告→None / `get_comparison` 返回基线)。改回「最早一份无基线」前先看这些测试。

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

### freezegun 测试 quirk

`backend/tests/core/test_logging_config.py` 的 `freezegun.freeze_time(...)` 调用必须传 `ignore=["transformers"]`,否则在跑全 suite 时 freezegun 会迭代 `dir(transformers)` 触发 `RuntimeError: cannot import name 'pil_torch_interpolation_mapping'`(pinned `transformers==4.51.3` 已移除该名字,而 freezegun 不捕获 RuntimeError)。未来其它测试用 `freeze_time` 也要加此 ignore 列表。

---

## 批量并行 worker(2026-09-03 起)

**事实**: `start.sh` 每类 worker 起多个进程,默认并发 parse=2 / interp=3 / extract=1,可经 `WORKER_PARSE` / `WORKER_INTERP` / `WORKER_EXTRACT` 环境变量覆盖(`ensure_workers()` 按 pgrep 现有数补足差额)。

- 每个 worker 的 stdout 日志**带序号后缀**:`/data/logs/worker-parsing.<i>.stdout.log`(worker-interpretation.<i> / worker-extract.<i> 同理)。**旧单文件日志 `worker-parsing.stdout.log` 重启后不再出现**,新名字一律带序号。
- 每个 worker 的 pidfile 同样带序号:`/tmp/start-sh-worker-<name>.<i>.pid`。
- worker cmdline 拼 `# $BACKEND_DIR`(WORKER_TAG)标记,`pgrep`/`pkill`/`cleanup()` 只精确匹配**本 checkout** 起的 worker,不误杀其它 checkout(如 `/home/wjyy2/hospitalKnowledgeBase`)。

---

## RabbitMQ vhost 统一到 `/`(2026-08-30)

**事实**: `backend/app/config.py` 有 `RABBITMQ_VHOST: str = "/"` 字段,`app/core/rabbitmq.py` 的 `_connect()` 通过 `virtual_host=settings.RABBITMQ_VHOST` 连接。`backend/.env` 显式 `RABBITMQ_VHOST=/`。

**历史教训(2026-08-30 故障)**: 曾有 worker 从旧 checkout `/home/wjyy2/hospitalKnowledgeBase` 启动,其 `.env` 是 `RABBITMQ_VHOST=hospital_dev`,而 `/data/project` 后端当时**没有** vhost 配置 → 发布到 vhost `/`,消费在 `hospital_dev`,任务永远 `queued`。现已将 `/data/project` 侧显式固化 vhost 到 `/` 与旧环境对齐。

**切换 vhost 的方法**: 只改 `backend/.env` 的 `RABBITMQ_VHOST`,并重启 **backend + 三个 worker**(report/interpretation/extract),保证生产消费同 vhost。改完用 `rabbitmqctl list_queues --vhost <vhost> name messages consumers` 核对两侧(0 积压、consumers≥1)。

**注意**:
- `/data/project` 代码**没有** `app/modules/risk` 模块(旧 `/home/wjyy2` 有)。解读流程不发布 risk 消息、不写 `disease_hit`;`high-risk` 接口读 `report_interpretation`(overall_level=="red"),不依赖 risk worker。故新架构不启动 risk worker。
- worker 启动必须 `cd backend` 后 `setsid nohup .venv/bin/python -u -c "from app.modules.<...> import start_worker; start_worker()" > /data/logs/worker-*.stdout.log 2>&1 < /dev/null &`,否则 `import app` 会因相对路径/`PYTHONPATH` 失败。

---

## MedGo vLLM 不能加 repetition_penalty>1.0 (2026-08-30)

**事实**: `start.sh` 的 MedGo vLLM 启动命令,`--override-generation-config` **只能设 `temperature`**,不能带 `repetition_penalty`(尤其是 >1.0)。

**历史教训(2026-08-30 故障)**: commit `3c63ad9` 曾在 override 里加 `{"temperature": 0.2, "repetition_penalty": 1.2}`。vLLM 重启后,MedGo 解析体检报告时**只抽出总检结论页的 3~5 个"偏高"异常项**,化验单的 70+ 项指标(数值/参考范围)全部丢弃 → 报告解读只基于残缺指标判定 green、无异常。症状:同一份 PDF 在旧 vLLM(无 override)能抽 72 项,新 vLLM 只抽 3 项;文本型 PDF(`_pdf_has_text`)走 `_build_parse_prompt` LLM 抽取受影响。

**根因**: MedGo 对长 JSON 列表输出时,`repetition_penalty=1.2` 过度抑制重复 token 模式,导致模型提前只生成首项(总检结论)就收尾。temperature 0.1→0.2 本身无影响(实测无关)。

**验证方法**:
```bash
# rp=1.2 (坏) → ~3 项
# rp=1.0 (好) → 72~74 项
curl -s http://localhost:8004/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"/data/models/MedGo","messages":[{"role":"user","content":"<解析prompt>"}],"max_tokens":16384,"temperature":0.2,"repetition_penalty":1.0}'
```

**重跑受影响报告的方法**: `/tmp/reparse.py <task_id...>`(删除旧指标+解读 → 重跑 `process_task` → 自动投解读)。注意 MedGo 生成 70+ 项 JSON 每份约 2~3 分钟,`setsid nohup` 后台跑。

