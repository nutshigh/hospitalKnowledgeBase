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

## 检后随访表(2026-09-07 起)

新表 5 张,同样须在三处 DDL 源保持一致:`infra/mysql/init/01_template_db.sql`(平台库
`followup_template` / `followup_template_question`,平台统一维护单套激活模板)、
`start.sh` DDL 块与 `infra/mysql/init/02_hospital_created.sql` 存储过程(租户库
`followup` / `followup_question` / `user_notification`)。存量 5 库迁移见
`backend/scripts/manual_migrations/006_followup.sql`。

- 触发:解读 worker(`interpretation/worker.py`)在 `run_interpretation_agent` 成功后同步调
  `try_generate_followup`;仅 `overall_level in (red,yellow)` 且此前无该 report_id 随访时生成。
- 生成把平台激活模板问题 + 黄/红指标(`indicator_judgment.color_level`)快照进租户库
  `followup_question` / `followup.recheck_indicators_json`,并写一条 `user_notification`
  (`category=recheck_reminder`),同事务;无激活模板时跳过(记 `app.followup`)。
- 用户侧接口在 `backend/app/modules/followup/router.py`,前缀 `/api/v1/followup` 与
  `/api/v1/notifications`;全部 `role='user'` + 双锚定,App(app-login)直接可用,无真推送。
- 删除报告时 `report/router.py::delete_report` 调 `delete_report_followup` 清理三张关联表。

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

## 报告跨报告对比 → 我的页健康变化总览(2026-09-09 起)

**事实**: 2026-09-09 起报告对比功能从报告详情页挪到用户端「我的」tab,由新增 `GET /api/v1/profile/change-overview` 支撑
(`backend/app/modules/user_profile/router.py` / `service.py::get_change_overview`)。

- **窗口选取**: 自动对比该锚定(user_id 后六位 + name)按 `report_date` 升序、仅 `status='completed'`
  的最近 `PROFILE_TREND_REPORT_LIMIT` 份报告(默认 3,`backend/app/config.py`);`report_date` 为 NULL 视为最旧放前。
  不足 2 份返回 `reason=insufficient` 降级(不入缓存)。
- **响应结构**: 自包含,含 `reports`(report_id/report_date/overall_level/红黄绿计数)、
  `key_indicators`(窗口内出现过红/黄的每一项指标,含血常规子项;与指标走势同口径:
  最近异常红>黄、同级按极差降序、再按标准名;`PROFILE_TREND_MAX_ITEMS` 默认 10 截断)、`summary`(四键 `trend_summary`·`conclusion`·`suggestions`·`precautions`,
  MedGo 生成,宽容解析失败则返回 None 且不写缓存)。`role='user'` 的 app-login token 可直接调用。
- **缓存**: 窗口最新一份的 `report_interpretation.comparison_summary` 存 JSON
  `{signature:[{report_id,interp_id}], fingerprint, payload}`;signature、fingerprint 与当前窗口一致才复用,
  旧纯文本/签名不符/指纹不符 → 重新生成并写回。
- **worker 钩子**: 解读 worker(`interpretation/worker.py`)在解读完成后调
  `service.ensure_change_overview(db, report_id)` 预热缓存,异常吞掉不冒泡。`comparison_baseline_id` 列不再使用(写 NULL)。

**已退役(勿再引用)**: `GET /profile/compare`、`GET /profile/ai-summary` 两路由与
`frontend/packages/user-portal/src/components/ComparisonCard.tsx` 均已删除;worker 旧钩子
`try_generate_comparison_summary` 不存在。`_auto_select_baseline` 仍在(供 `/profile/overview` 的
`user_summary.baseline_date`)。

**测试**: `backend/tests/user_profile/test_change_overview.py`(窗口/缓存签名命中与失效/降级/LLM 失败不写缓存)、
`backend/tests/test_interp_worker_bulk.py::test_comparison_summary_failure_doesnt_break`。改回比较式旧功能前先看这些测试。

- **走势/变化总览展示窗口内全部红黄指标(含子项)(2026-09-10 起)**:窗口内出现过红/黄的每一项指标(含血常规子项,如 血小板比积（PCT）、血小板平均体积（MPV）)都以自身规范名在 `get_overview` 走势与 `/profile/change-overview` 的 `key_indicators` 独立成系列,每报告 ≤1 点;两处入选/排序/上限一致(最近异常红>黄再极差降序、`PROFILE_TREND_MAX_ITEMS` 默认 10)。正常(绿色)指标不出现(如全绿的血小板计数)。`app/core/term_normalizer.py::is_child_item` 保留但其定义不再被 profile 用于过滤;`_split_item_name_collisions()` 仍对同报告同 key 多 item_name 的脏数据拆独立系列并告警。存量标准名回填脚本 007 已对 hospital_1/H001/H002 执行;H003/H004 未回填。

- **变化总览/走势取窗差异(已知)**:走势取最近 N 份报告(不限解读状态);变化总览取最近 N 份**已完成解读**报告。两者入选规则/排序/上限已对齐(2026-09-10),取窗仍可能不同。规则口径变更后需一次性清空存量缓存:`backend/scripts/manual_migrations/008_clear_change_overview_cache.sql`(对全部 tenant 库各执行一次)。修改 `PROFILE_TREND_MAX_ITEMS` 或入选规则后需同样清一次缓存(否则旧 payload 保留旧条数/口径);且 AI 总结的输入指标集合已随之改变(异常子项也会进入、单报告异常会进入)。

| 病种规则新增表(2026-08-15 起) | 用途 |
|------|------|
| `disease_mapping`(增强) | 新增 `source`(CENTRAL/LOCAL)、`match_level`(YELLOW/RED)、`match_deviation`(偏高/偏低)三列;旧库需 ALTER(start.sh 已带) |
| `disease_rule` | 异常指标→病种 严格AND组合规则(`rule_code` 唯一,`member_items` JSON 为 `[{name, min_level, deviation}]` 结构化成员) |
| `disease_hit` | 报告粒度病种命中固化(uk: `report_id+disease_name`;冗余 user_id/unit_name/report_date 供统计直查) |

**风险引擎链路**:解读 worker 完成后 publish `risk.normal`(经 QUEUES 特例绑定路由到 `risk.hit` 队列,见 `rabbitmq.py` 中 `"risk.normal": "risk.hit"`)→ risk worker 异步计算 → `disease_hit` 落库(先查后插幂等,失败走 retry 队列)。引擎为纯 DB 计算无 LLM、无 bulk 窗口。统计端点(`disease_service.py`)disease 模式已改直查 `disease_hit`(indicator 模式不变)。
**归一化**:中央归一化表在 `term_normalizer.py`,`_store_abnormalities` 落库时归一化表命中优先(未命中回退 LLM 名);同名跨科目指标(葡萄糖/白细胞/红细胞)按 result/unit 消歧(血检 vs 尿检)。中央种子: `app/modules/risk/seed.py::sync_central()`(**87 映射 + 6 规则,2026-08-19 版**;审核清单在 `app/modules/risk/candidates/`)。**seed 不在启动链路,改 seed.py 后需手动跑一次 `sync_central` 到各 tenant 库**。
**匹配口径(2026-08-19 决策)**:①肿瘤标志物/超敏肌钙蛋白类单指标 **RED 才命中**(降假阳性),组合命中可承载低可靠性线索;②贫血收敛为 **Hb 为核心指标**直接命中,MCV/MCH/MCHC/RDW 不再单独命中(旧 CENTRAL 条目由 sync_central 自动停用);③**结论型条目(source='conclusion')支持子串匹配兜底**(标准名 ≥3 字、双向包含,覆盖"脂肪肝(中度)"类 LLM 自由文本),生化/指标型仅精确匹配;④统计层白名单剔除非疾病条目(`disease_service.py::_STATS_EXCLUDED_DISEASES`:肥胖症/龋齿/牙周病/扁桃体肥大/屈光不正/外耳道耵聍/肝内钙化灶/胆囊壁胆固醇结晶/甲状腺囊性结节),映射表保留供解读链接。
**CRUD**:`/api/risk/*`(服务间鉴权 `require_service_client`,供 sz-mana 填本院私有规则)。

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

## start.sh 启动模式(2026-09-03 起)

**事实**: `start.sh` 已从「前台阻塞 + Ctrl+C 全杀」改为「**启动即返回 shell + 独立 `--stop`**」。

- `bash start.sh` = 启动 Docker 中间件 + 模型 + 后端 + workers,打印完成横幅后**返回 shell**(服务 nohup 后台常驻)。不再有末尾 `wait`,不再前台阻塞。
- `bash start.sh --stop` = 停应用层全部服务(模型/后端/workers,worker 按 `WORKER_TAG` 只停本 checkout);**Docker 中间件保持运行**(需停: `cd infra && docker compose down`)。
- 不再 `trap cleanup SIGINT SIGTERM`:启动途中 Ctrl+C 只中断脚本本身,nohup 子进程不受影响;停服一律用 `--stop`。
- `start_front.sh` 同构:`bash start_front.sh` 启动返回、`bash start_front.sh --stop` 停前端三门户。
- 模型/后端/worker 的启动逻辑与 GPU 分配未变,见上文各节。

---

## RabbitMQ vhost 统一到 `/`(2026-08-30)

**事实**: `backend/app/config.py` 有 `RABBITMQ_VHOST: str = "/"` 字段,`app/core/rabbitmq.py` 的 `_connect()` 通过 `virtual_host=settings.RABBITMQ_VHOST` 连接。`backend/.env` 显式 `RABBITMQ_VHOST=/`。

**历史教训(2026-08-30 故障)**: 曾有 worker 从旧 checkout `/home/wjyy2/hospitalKnowledgeBase` 启动,其 `.env` 是 `RABBITMQ_VHOST=hospital_dev`,而 `/data/project` 后端当时**没有** vhost 配置 → 发布到 vhost `/`,消费在 `hospital_dev`,任务永远 `queued`。现已将 `/data/project` 侧显式固化 vhost 到 `/` 与旧环境对齐。

**切换 vhost 的方法**: 只改 `backend/.env` 的 `RABBITMQ_VHOST`,并重启 **backend + 三个 worker**(report/interpretation/extract),保证生产消费同 vhost。改完用 `rabbitmqctl list_queues --vhost <vhost> name messages consumers` 核对两侧(0 积压、consumers≥1)。

**注意**:
- **(合并后已更新 2026-09-13)**: 本 checkout 现已包含 `app/modules/risk` 模块;解读 worker 完成后会 publish `risk.normal` 消息,`start.sh` 同时启动 risk worker(`worker-risk.stdout.log`,无 WORKER_TAG),计算并写 `disease_hit`。`high-risk` 接口仍读 `report_interpretation`(overall_level=="red"),不依赖 risk worker。若部署环境不需要风险引擎,可关闭 risk worker 启动段(不影响报告解读主链)。
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

## 医生工作台跨院查看(X-Hospital-Id)(2026-09-08 起)

**事实**: doctor-portal 是单医院视图,靠 `get_current_user` 从 JWT 取 `hospital_id` 选库。
2026-09-08 起支持**请求头 `X-Hospital-Id` 覆盖**:

- 契约:doctor/admin 角色带 `X-Hospital-Id: <hospital_id>` 且该院在 `hospital_tenant.is_active=1`
  时,后端用请求头医院覆盖 JWT 医院(一处改动在 `dependencies.py::get_current_user`);未知/停用
  医院静默回退 JWT 医院;`role='user'`(患者端/App)一律忽略该头,绝不跨院。
- 跟随 `X-Hospital-Id` 的:走 `get_current_user`、用 `CurrentUser.hospital_id` 选库的院级查询 ——
  reports(list/detail/delete/upload)、interpretations(含 high-risk)、followup、chat、profile。
  **statistics / dispatch 不在此列**:它们不依赖 `get_current_user`,直接读 context var
  `current_hospital_id`(`app/middleware/hospital_context.py`,仅 `get_current_user` 内 set),
  无前置鉴权请求会 400,切换医院对它们不生效 —— 既有缺口,另行接线,不在 2026-09-08 改动范围。
- 医生端(`doctor-portal`)Header 顶部有医院切换器:选项来自 `GET /api/v1/tenants`
  (该接口已对 doctor 开放),选择后写 `localStorage['doctor_active_hospital']` 并经
  axios 拦截器自动带头,页面重载切库。患者端(3001)不发该头。
- 报告管理列表(doctor/admin 全量视图)过滤 `task_status='failed'` 与
  `parsed_name/name/report_date 全 NULL` 的空壳残留行(演示库白行来源);
  患者按锚点查询不受影响。
- 历史演示库(hospital_H001,3056 条)含大量空壳/失败残留行,列表已隐藏,未批量清理。

## 结论提取/展示管线的医院模板适配(2026-09-03 起)

**入口文件**: `backend/app/modules/report/service.py`(切段/重排/提取) +
`backend/app/modules/report/report_profiles.py`(医院模板档案)。

**新医院模板适配流程(不要再改主逻辑正则打补丁)**:
1. 样本 PDF 放仓库根 `体检报告样例/` 对应子目录
2. `backend/tests/modules/report/conclusion_samples.py` 加一条记录
   (must_contain/forbidden/expected_titles/expected_summary_titles, 参照现有条目)
3. 布局/词表特殊时在 `report_profiles.py` 的 `PROFILES` 登记:
   - `keywords`: 机构名(报告页眉/首页出现), 全文子串匹配
   - `visual_sort: True`: 页内多栏混排需视觉坐标重排(现仅防城港市中医医院)
   - `extra_break` / `extra_skip`: 追加的段尾断点 / 页眉行(默认词表已覆盖绝大多数)
4. 验收: `cd backend && .venv/bin/python -m pytest tests/modules/report/test_conclusion_regression.py -q`

**回归集**: 26 断言(2026-09-03),覆盖广西 11 家 + 北京 2 人;测试走**生产同款文本**
(每页带 `--- Page N ---` 标记;visual 模板按坐标排序)。改任何切段/重排/标题解析
规则后必须跑它,防"修 A 坏 B"。另有 `test_profile_visual_flag_consistent_with_samples`
强制样本 visual 标记与档案一致。

**关键行为提醒**:
- `_extract_pdf_text` 输出每页前带 `--- Page N ---`;**页分隔是 SKIP 不是 BREAK**
  (跨页结论必须连续, 柳州"…请到眼科验光矫正。"曾被 BREAK 截断, 2026-09-03 修)。
- `_extract_conclusion_async` / `_locate_findings_sections` 支持 `profile` 参数
  (extra_break_re/extra_skip_re), `process_task` 已按档案自动选 visual 提取。
- 小结标题(`序号+【】`)下的分行条目由 `_parse_summary_item_titles` 确定性解析
  (桂林左右叶结节), 不依赖 LLM; 提取对账: LLM 漏提(safety-net 补过条目)时
  自动二次抽取取并(见 `_postprocess_extracted_items` 与 double-pass 段)。
- 编号标题解析 `_parse_numbered_titles` 按顿号切分("牙龈炎、牙结石"→"牙龈炎",
  两异常分开合理), 测试期望与其一致。

---

## OCR 服务端口与本 checkout 口径(2026-09-13 核实)

**事实(以本 checkout `/data/project/hospitalKnowledgeBase` 为准)**:
- OCR 端点由 `settings.OCR_BASE_URL` 决定,唯一客户端是 `app/core/vlm_client.py::VLMClient`
  (模块单例 `vlm_client = VLMClient()`),**全仓库无任何代码硬编码 8006**。
- 本 checkout `backend/.env:54` `OCR_BASE_URL=http://localhost:8001`,`start.sh` 不覆盖它,
  运行中的 backend/workers 环境里也没有该变量 → **实际走 8001**,由 `start.sh` 用
  `backend/paddle_venv` 启动(GPU3,`PADDLEOCR_VL_MODEL`/`PP_DOCLAYOUT_MODEL` 见 start.sh)。
- 启动命令(8001 若丢失):
  ```bash
  cd /data/project/hospitalKnowledgeBase/backend
  CUDA_VISIBLE_DEVICES=3 PADDLEOCR_VL_MODEL=/data/models/PaddleOCR-VL-1.5 \
  PP_DOCLAYOUT_MODEL=/data/models/PP-DocLayoutV2 \
  /data/project/hospitalKnowledgeBase/backend/paddle_venv/bin/python \
  -m uvicorn paddle_ocr_service.main:app --host 0.0.0.0 --port 8001
  ```
- **已知故障**: 8001 跑一段时间后 `/ocr` 会 500,报
  `RuntimeError: int(Tensor) is not supported in static graph mode`(paddle 静态图)。
  **重启 8001 即恢复**(2026-09-13 实测:重启后真实报告页 `/ocr` 返回 HTTP 200 + 正常
  markdown)。批量重跑扫描件前先 curl 测一页 OCR。
- `POST /ocr`,body `{"image_base64": ...}`,响应取 `markdown`(multipart 会 500)。

**8006 的历史说明(勿混淆)**: 8006 是 `/home/wjyy2` 旧 checkout 手工起的 PaddleOCR 实例,
只有那边 workers 的环境里带了 `OCR_BASE_URL=http://localhost:8006` 才走它;本目录代码
从不引用 8006, 该实例已随 wjyy2 服务停用。本文件历史小节里若仍出现"走 8006 / 重启 8006",
均为该历史环境的记录,在本 checkout 应读作"OCR 服务(当前 8001)"。

---

## 报告模板档案与回归集的形态治理(2026-09-04 起)

**医院档案(`backend/app/modules/report/report_profiles.py`)字段**(全部可选, 命中即合并):
- `keywords`(必填匹配键)、`visual_sort`(页内多栏混排)、`extra_anchor`(追加结论段标题行
  正则 —— 厦门弘爱"自测问卷发现的主要疾病及健康危险因素:"通用词表拼不出)、
  `extra_skip`(追加页眉/字段标签行 —— 德宏"检查所见:/总检建议:"每条约出现一次,
  会当细节起点/锚点吞正文, 跳过标签行后内容保留)、`extra_break`(追加段尾断点)。
- 已登记: 防城港市中医医院(visual)、厦门弘爱(extra_anchor)、德宏州人民医院(extra_skip)。
- 扫描/验收命令:
  `.venv/bin/python scripts/scan_report_quality.py [PDF|目录]` —— 无 LLM 质量体检,
  五类可疑判定(定位失败/脏行/过短/重排异常/零条目); 样例目录含"各地汇总/"新地区样本。

**回归集分层**(`tests/modules/report/`): 精修样本(强断言 must/forbidden/titles/
summary/指标层) + `smoke: True` 冒烟样本(弱断言: 能定位/够长/无通用脏行)。
指标层断言族覆盖需求①(挖除结论段→行式/列式提取, 防切段规则漂移指标)。

**混合页 OCR(2026-09-04 立项)**: `_extract_pdf_text(hybrid=True)` 对"文本<100字
且含≥2图"的页自动调 PaddleOCR(端点 = `OCR_BASE_URL`,本 checkout 为 8001)补文本 —— 福建第二(文本化验页+图片结论页)
从"0 字结论"恢复出主体条目(1-9 号异常, 2/3/5 号 OCR 偶漏)。`process_task` 文本
分支已 hybrid=True。OCR 页失败不影响整份。

**已知限制/待办**: 福建第二 OCR 偶漏
条目; 齐鲁青岛等"OK"样本多为冒烟级(未逐字精修); visual_sort 仍人工标注。
- 茂名人民(2026-09-04 已修): ★ 标记异常 + 分散排版标题("医 生 建 议"/"检 查 综 述") —— 锚点判定先压缩行内空白; _parse_numbered_titles 支持 ★ 前缀。

---

## 指标提取/总检异常链路与验证纪律(2026-09-05 起, 含两次事故教训)

### 展示链路事实(排查"改了看不到"先看这里)
- 前端 `ReportDetailPage.tsx`: `interpretation?.indicators?.length ? interpretation.indicators : report.indicators`
  —— **有解读记录时只显示解读固化快照 `indicator_judgment`**, 忽略 `/reports/{id}` 的
  `report_indicator`。因此改了指标表(process 提取)后, **已解读报告必须重解读/回填才可见**。
- API 8005 进程如未真正重启(曾出现 kill 失败、老进程 9-04 起仍活着), 服务端代码不生效,
  排查"前端没变化"时先 `ps -eo pid,lstart,cmd | grep 8005` 核对启动时间。
- 结论条目展示前还有两层去重(会"剔除"结论条目, 需区分是 bug 还是设计):
  `interpretation/service.py::get_judgments_with_indicator_detail` —— ①结论 vs 指标黄区
  同名/模糊匹配剔除; ②disease_mapping 撞车剔除。诊断型结论豁免: 名称以
  (血症|病|症|炎|瘤|癌|肿|硬化|息肉|结节|结石|囊肿|异常)结尾且含指标名子串 → 放行;
  综合征诊断("血脂异常")不以指标名作子串 → 无精确同名即放行(德宏需求)。
  `_share_prefix5`: 剥括号/结论/测定 后缀后前 5 字相同 = 同一实体 → 剔结论侧
  (福建"乙肝两对半(第1,2,4,5项阳性)" vs 指标"乙肝两对半结论")。

### 两次"结论区全绿"事故(2026-09-05 深夜 / 2026-09-06)—— 根因与根治
- 机制: 结论区条目 = `report_indicator.raw_text IS NOT NULL` 占位行 + judgments(source='conclusion')。
  重解读时若 DB 残留 raw 占位行(无 judgments 或旧 judgments 被级联删), 解读 triage 会把
  这些无值无参考的行当普通指标判 **green**; 解读完成后的 backfill(worker running-skip 分支)
  见"已有 raw judgments"即跳过 → 结论条目以绿留存, 前端全绿。
- **根治(2026-09-06)**: `interp_graph.py::load_indicators` 查询加 `AND raw_text IS NULL` ——
  结论占位行**永不参与解读 triage**; 结论条目统一由 worker backfill
  (`_extract_abnormalities_async` + `_store_abnormalities`, 落 yellow/red)管理。
- 教训: 重解读前删 interpretation 时若 raw 占位行未清, 旧代码必复现(已两次)。
  验证修复必须**端到端重跑**(见下节), 手动改 DB 后刷新 ≠ 代码正确。

### 验证纪律(本仓库最高优先级约定)
0. **分层验证(2026-09-07 策略确认)**: 中间迭代(提取器/词表/组装相关改动过程)用
   **离线组装仿真**验收 —— `.venv/bin/python scripts/offline_indicator_assemble.py <pdf> [--report-id N]`
   —— 与 service.process_task 组装段**同构**(rows+列式/块表+signals+__auth 抑制+
   normalize_indicators), 秒级复现落库前产物, 可直接与 DB 已落库行 diff(仅DB有/仅脚本有)。
   **端到端重跑(重 process+解读+落库)只在用户明确发令后执行**——用户验收节奏由用户
   掌控。**端到端之前的前端更新一律用离线仿真产物手动同步 DB**(预览态: 替换指标行、
   清 interpretation/judgments, 前端直读指标区; 黄/绿判定与结论区以端到端为准)。
   离线仿真结果 ≠ 端到端结果的已知差异: ①hybrid OCR(离线默认不开 OCR, 图片页行可能
   缺); ②judgments/结论 backfill/展示去重只有端到端产生。
   **护栏触发规则(2026-09-09 用户确认, 取代 09-07 版)**: **取消自动全量护栏**。
   - 迭代期(提取链/滤卡/fill/兜底/去重/词表补充等改动): **不跑护栏**。主验证 =
     **离线结论仿真**(`scripts/offline_conclusion_assemble.py <pdf> --report-id N [--sync]`,
     单份 1-2 分钟; 指标侧 `offline_indicator_assemble.py`) + **秒级纯函数断言**
     (直接调 `_filter_junk_abnormalities`/`_expand_title_segments`/`_parse_numbered_titles`/
     fill 等确定性函数, 喂构造输入断言输出, 不调 LLM 不解析 PDF)。
   - **提醒用户跑全量 103 的条件**: 改动触碰**回归集有覆盖**的解析层 —— 切段/锚点/
     extra_break|skip|anchor/reflow/visual 排序/profile 匹配/`_parse_numbered_titles` 与
     `_parse_summary_item_titles` 及其词表(`_FINDING_TITLE_RE`/`_SAFETY_NET_JUNK_RE` 同时喂
     标题解析)/判定层/指标归一。此时 Agent 提醒用户, **跑不跑由用户决策**。
    - **不提醒也不跑**(回归集无断言, 跑了验不出): junk 滤卡/fill/expand/cross-dup/store 层
      等提取链内部 —— 这类改动靠离线仿真 + 纯函数断言兜底。
    - **秒级纯函数断言文件**: `tests/modules/report/test_extraction_units.py`(2026-09-09 建,
      54 断言, 0.5s)—— 覆盖上述盲区。其中约 40 条为**纯函数行为契约**(与具体医院无关,
      任何报告改动都应保住); 约 14 条为 USER6 场景快照(锁死已修缺陷不复发)。
    - **新报告/新模式扩展机制**(2026-09-09 用户确认): 新 PDF 用离线仿真探测; 修复新模式
      问题后, ①把**最小复现片段**(几行构造文本 + 断言)补进 test_extraction_units.py ——
      该模式从此受保护(测试随修复变厚, 不随报告批次作废); ②整份 PDF 加进
      `conclusion_samples.py`(文本层弱断言 min_len/无脏行/能定位), 进结论回归样本库。
    - 全量命令: `pytest tests/modules/report/ tests/test_safety_net.py -q`
      (无 LLM, 3-5 分钟; 结论回归 54 + 指标护栏 2 文件)。
    - **LLM 侧改动节奏(2026-09-10 用户确认)**: 改"LLM 输出 → 条目"环节(parse/滤卡/
      fill/junk-context 等, 如池州人民那轮)时, **不要每轮全链路真调 LLM**(一轮 ~67s×2,
      迭代 8-9 轮纯属浪费)。正确节奏: ①真调**一次**取真实 LLM 响应**快照存文件**;
      ②后续调优全部用"快照重放"(LLM 输出固定, 后处理确定性, 秒级一轮);
      ③收敛后最后真调一次确认 + 端到端一次。只有需要观察 LLM 新输出形态时才再真调。
      工具化(2026-09-10): `ABNORMALITY_LLM_SNAPSHOT_DIR=<dir>` 自动读快照重放,
      `ABNORMALITY_LLM_SNAPSHOT_SAVE=1` 采快照(用法见"新医院报告适配 SOP")。
      确定性层改动(词表/锚点/断点)不涉 LLM, 广西+北京式"回归直验 + 最后重投一次"仍成立。
   手动改 DB 仅限一次性清理/预览, 不作验收(两次"结论全绿"教训)。
   **改 service 组装逻辑必须同步 offline 脚本**(防漂移;
   校验 = 脚本输出 vs 某已 process 报告 DB 行 0 diff)。
1. **生成逻辑改动**(table_extractor / service 组装 / interp_graph / interpretation service /
   worker backfill)验收 = 端到端重跑: 上传路径或删 interpretation 重投, 让代码链路自动
   产出, 核对 DB/API 产物。**手动改 DB 得到正确展示不代表代码正确**。
2. 手动 SQL 修正只允许用于一次性脏数据清理, 之后仍要端到端复验一次。
3. 重启生效类改动(worker/8005): 重启后 `ps` 核对新 pid/lstart, 不要假定成功
   (start_workers.sh 对已运行的 8005 会跳过, 需先 kill)。
4. 回归命令(无 LLM, 3-5 分钟;**仅在用户决策后执行, 不自动跑**):
   `pytest tests/modules/report/ tests/test_safety_net.py -q`
   —— 结论回归 54 + 指标护栏 2 文件(广西/北京 12 家 36 断言 + 新 5 家/历史基线 10 断言)。
   已知唯一失败: `ctni`(广西人民"血清肌钙蛋白I 测定"名称内部空格, 能力未实现, 勿误当回归)。
   (注意: 本文件其它小节里历史遗留的"103 passed/护栏必跑/护栏内"字样不代表当前自动节奏,
   以本段验证纪律为准。)

### H003/H004 全量重跑与规则收敛(2026-09-12)

新代码批量重跑 13 份(H003 20/22-26, H004 21-27, 池州 26 在前), 端到端对照旧产物,
暴露并修复一批 LLM 波动/形态边界(单测 81, 全量回归 279 passed):
- **泛词无条件删**(`_dedup_generic_findings`): 纯泛词(结节/钙化灶/钙化/感染等)不是
  独立异常, 不再要求"同批有更长条目"(日照"钙化"漏网)。"肺结节"泛称限**同部位**
  (含"肺")才删, 防误删报告方真标题(马鞍山 `[CT 提示：肺结节]`)。
- **纯部位词黑名单**(`_ANATOMY_ONLY_RE`): 二尖瓣/三尖瓣/主动脉瓣等单独成条=LLM 碎名
  (弘爱"二尖瓣" vs 完整"二尖瓣、三尖瓣轻度反流")。
- **fill 拒绝扩展**: 句子式("不一定有临床意义…"走 fill 不受 filter 滤卡管)+
  "方法+提示+数字"残片("口腔科提示18"/"彩超检查提示1")。
- **方向词候选拒绝引导句**: 行首版块符(▍等)与"以下/按照/列出/诊断标准…"引导词
  (滨州"发现和其他异常"/"指标解读以下按照…"曾被切出为候选)。
- **方向词候选拒绝科普/建议连续句(2026-09-12 柳州回归)**: 柳州(石坤, H004-1)
  重跑被切出"检查化验结果略有异常/肝功能异常/①血脂异常/喝茶也可使血脂水平下降"
  —— 枚举行判据收紧: 含句号/分号的行不产、行首圈号①-⑩跳过、stem 拒绝科普连接词
  (可使|也可|是一种|者到|但是|因此|不主张|通过|如果|若)。**教训**: 方向词候选在
  fill 层(filter 之后, 不经 junk-context), 候选规则必须自带语义门槛; 新规则上线
  后需重跑**全量已适配报告**(不止历史批次)验收。
- **编号标题跨行未闭合 [**: "6、[甲状腺结节,\n考虑C-TIRADS3 类]" 拼接取段
  (马鞍山 LLM 波动时不再丢标题)。
- **OCR 500 处理(本 checkout 走 8001)**: 重启 OCR 服务(命令见"OCR 服务端口与本 checkout
  口径"节)后 OCR 恢复, 图片型报告(福建第二)结论页可正常提取; 批量重跑前先 curl 测一页 OCR。
- **批量脚本 quirk**: `scripts/rerun_h003h004.py` 轮询需每轮 `c.commit()`(pymysql
  REPEATABLE READ 下同连接不刷新快照会永远读旧状态)。
- **解读侧 tool-call 400 已根治(2026-09-12)**: H003-22(滨州)MedGo 生成截断
  tool_call → langchain 归档 `invalid_tool_calls`(此时 `tool_calls` 为空)→ 旧守卫
  `if not tcs: return` 漏过 → 坏串随历史回传 vLLM(function-wrap 校验 400, 单次挂
  12 分钟)。修复: `_guard_sanitize_message` 同时清 `invalid_tool_calls` 与
  `additional_kwargs["tool_calls"]` 原始坏串(合法消息原样透过); 解读/报告模型加
  `request_timeout=600`。单测 `tests/test_toolcall_guard.py`(4 条)。

### 弱切分按院声明(2026-09-12 用户拍板)

**背景**: "一行多发现"弱切分(`_parse_direction_phrases` 多方向行 /
`_parse_numbered_multi_findings` 编号行多发现)在未见文本上会误切科普/建议
(碎片多提), 而完全取消又会系统性漏提行内第二+发现(用户多轮追漏项均为此类)。
**方案(用户拍板)**: **默认关闭**, 仅对已验证存在该排版且需规则补漏的医院在
`report_profiles.py` 档案里声明 `multi_findings=True`(现:池州/福建省第二/广西人民)。
- 调用链: `_extract_abnormalities_async(conclusion, weak_candidates=...)` →
  `_postprocess_extracted_items(..., weak_candidates=)`; worker backfill 用
  `_weak_candidates_enabled(db, report_id)`(读原始 PDF 文本匹配档案; 纯扫描件
  提取为空 → 默认关, 靠【】标题/LLM); offline 脚本同构(profile 决定)。
- 新医院默认净度优先(无弱切分); 若验收发现"行内多发现漏提", 再评估是否声明。
- **护栏边界(用户已知晓)**: 护栏锁已验收产物, 发现不了新文本上的规则误切;
  分隔符/短建议语义冲突靠"声明制 + 保守默认 + SOP 人工验收"治理。

### H003 报告级三件套基线护栏(2026-09-12 建成, 09-12 口径修正)

- 文件: `tests/modules/report/test_h003_baseline_guard.py`(13 份全量: 1-7,20,22-26)
- **基线 = 用户验收过的 DB 结果快照**(`scripts/gen_h003_baseline.py` 从 DB 导出:
  指标黄红名单 + conclusion_text 哈希 + 总检异常名单); 测试**直接读 DB 比对**, 秒级。
- **口径修正原因**: 首版基线用"离线链产物", 不含 store 的跨线去重(cross-dup)且
  混入迭代代码, 与 DB 验收态差 39 处(绝大多数是 cross-dup 设计内差异)。现以 DB
  为唯一基准: 重跑后产物与验收态有任何不同即红 → 人工审 diff。
- 更新流程: 重跑 gen(从 DB 导出) → `git diff baselines/*.json` 人工审 → 提交。
- 迭代顺带修真问题: ①"腹型肥胖"黑名单误杀(防城港一 DB 真条目)已移除, 贵港多提
  改由 profile 切段解决; ②"体重指数>24"数值碎片名加滤; ③池州"粘液丝"按口径回绿
  (弱阳性≠异常, 仅提示列 ± 判黄)后重跑入基线。

### H004 报告级三件套基线护栏(2026-09-12 建成, 09-12 口径修正)

同 H003(见上), H004 全量 13 份(1-6, 21-27)同样以 **DB 验收快照**为基线:
- 文件: `tests/modules/report/test_h004_baseline_guard.py`; 基线: `h004_baseline.json`
- gen: `scripts/gen_h004_baseline.py`(从 DB 导出, 秒级); 更新: 重跑 gen → 人工审 diff
- 至此 H003(13)+H004(13)= 26 份已适配报告全部锁入 DB 口径三件套基线。

### 修复 match_profile 浅拷贝污染(2026-09-12, 重大)

**现象**: 护栏测试暴露 —— 同进程内先跑 `_extract(日照 24)` 后, 柳州切段 1466→2503。
**根因**: `report_profiles.match_profile` 用 `dict(DEFAULT)` **浅拷贝**, 命中档案时
`merged.setdefault(f, []).extend(list(prof[f]))` 直接 extend 了模块级 DEFAULT 的
**共享 list** → 该档案的 extra_break/skip/anchor **永久写进 DEFAULT**, 之后所有报告
(长驻 worker 内)继承 → 跨报告切段污染(顺序依赖, 难复现)。
**修复**: `merged = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT.items()}`
**启示**: 此前多轮"重跑后结果漂移/怪结果"部分可能源于此; 凡模块级可变默认值,
合并前必须深拷列表。

### 新医院报告适配 SOP(2026-09-10 确立, 目标: 一份一管线, 不返工)

1. **样本入库**: PDF 放 `体检报告样例/` 对应目录; `conclusion_samples.py` 登记
   (精修样本强断言 must_contain/forbidden/expected_titles; 冒烟样本 `smoke: True`
   + min_len —— 回归集断言是"必须包含 + **不得多产**"双向)。
2. **快速探测(秒级, 不调 LLM)**:
   `offline_indicator_assemble.py <pdf>`(指标侧组装)
   `offline_conclusion_assemble.py <pdf> --report-id N --db H00X`(结论切段/提取产物)。
   需要看页面结构时 `LAYOUT_DEBUG=1`; 图片型结论页 `--hybrid` + `OCR_BASE_URL=http://localhost:8001`(本 checkout 口径)。
3. **LLM 快照采一次(只在需要观察 LLM 输出形态时)**:
   ```bash
   ABNORMALITY_LLM_SNAPSHOT_SAVE=1 ABNORMALITY_LLM_SNAPSHOT_DIR=backend/artifacts/llm_snapshots \
     .venv/bin/python scripts/offline_conclusion_assemble.py <pdf> \
     && ABNORMALITY_LLM_SNAPSHOT_DIR=backend/artifacts/llm_snapshots \
     .venv/bin/python scripts/offline_conclusion_assemble.py <pdf>   # 之后走重放, 秒级
   ```
   重放命中日志: `abnormality snapshot replay: <sha1>.json (n)`; 不命中才真调。
4. **问题修复的落地三件套**(按已踩坑教训固化):
   - 结构差异 → `report_profiles.py` 数据化(锚点/断点/visual_sort/字段), **禁止按医院名写分支**;
   - 名称形态差异 → 检查是否可被 `_write_norm`/`_cmp_norm`(入口化归一名)或
     `_parse_direction_phrases`(多方向行)/`_parse_numbered_titles`(编号/【】/★/子编号)
     规则覆盖, 优先改规则而非加特例;
   - 最小复现片段补 `tests/modules/report/test_extraction_units.py`(纯函数断言)。
5. **收敛验收**: 快照重放全绿后, **最后真调一次**(删掉/绕开快照)确认 + 端到端一次
   (重 process + 删 interpretation 重投, 见验证纪律); 解析层改动按纪律提醒用户跑全量
   回归(`pytest tests/modules/report/ tests/test_safety_net.py -q`)。
   改 worker 代码后必须重启 worker(`scripts/start_workers.sh` 前先 kill 旧进程)再验证。


### 结论侧工程化收尾(2026-09-09, 用户四项决策)
1. **医院特判审计结论**: 逻辑层无按医院名的运行时分支 —— 布局/锚点/断点/综述插页/
   表格结论/视觉排序等特判已全部数据化在 `report_profiles.py`(PROFILES); 代码注释中的
   医院名(日照/茂名/齐鲁/莆田…)是**通用规则的出处注记**, 不代表医院分支, 勿误迁。
2. **建议归属校验**(service.py `_validate_suggestion_ownership`, 提取链尾部确定性步):
   LLM 偶发把某条目长建议串复制给相邻条目(茂名 25 前列腺建议曾贴给 碳13/窦缓/电轴)。
   判据: 建议含器官专属词(`_BODY_ORGANS_RE`, 排除"心脏负担"类搭配)而条目名不含 → 定位
   建议所在原文行: L1 该行是别的 ★/●/【 标题行且标题不含条目名 → 清空; L2 距条目名
   行 >3 行且建议不含条目名 → 清空。通用生活建议句(低盐饮食/适度锻炼)不校验。单测见
   test_extraction_units.py。
3. **sync diff 报告**: offline_conclusion_assemble --sync 自动产出 提取产物 vs 上次落库
   对比(新增/移除/suggestion 变化/报告方标题缺失清单), 统一落 **backend/artifacts/conclusion_diff/**
   单文件夹(不入库, 仅诊断)。
4. **总检异常 ↔ conclusion_text 原文行映射**: interpretation
   `get_judgments_with_indicator_detail` 对 source=conclusion 条目附加
   `origin_row`/`origin_line`(原文行号+行文本), 前端 ReportDetailPage 在结论条目名下
   灰字显示"原文: …"(去重后展示的条目才有)。

### 指标提取新增规则(table_extractor.py, 2026-09-05/06, 均在护栏内)
- `_strip_history_compare_table`: 独立标题"历年对比"(及变体)起的**双年值列表整段挖除**
  (德宏: 该表无异常标志, 参考左值/历史值曾被当指标行 57/3.4/0.4); 终止 = 段落标题
  (本次体检结论/体检综述/总检建议/签名/页断), 60 行安全阀。行式/列式入口幂等调用。
- `≤≥` 单侧参考("≤1"/"≤0.06"): `_RANGE_RE`/`_parse_ref`/列式 assemble 均支持。
- 名称: `#`(BA#)、`▲△★` 前缀 0-2 个、纯 ASCII 斜杠缩写(FPSA/TPSA, 排除单位尾, `(?i:...)` 组限定)。
- 行式: 值行后循环消化 unit/ref(跳过提示文字"偏高"/箭头/单数历史行); 名称前一行 RANGE 作
  ref(反列序, `_consumed_ref_lines` 防误配); 英文括号行并入名称("(A-TPO)")。
- 信号通道: 提示列文字行("偏高"独立行, flagtext)成信号; word 通道综述多异常全扫
  (`_extract_summary_abnormal_pairs`, 值以 ≥/≤ 开头=判定线说明, 不产); 列式行 flag 与 ref
  由 row/signals 提升补齐; 定性"…(定性)结果阳性"判 f1(福建乙肝五项 `*` 标志语义)。
- 组装(service.py process_task): col 行 signal_flag = max(col,row,signal); col ref/unit 缺失
  用行式同 key 补。`_assemble_column_row` 数值分流: 分隔范围永不作 result; 值后单限=ref;
  块首符号值(>1000/<0.02)=result。**勿再引入"双年值列取最后"逻辑**(2026-09-06 已回退,
  正确路径是挖除历年对比表)。

### 总检异常提取(结论区)修复(2026-09-05, service.py `_extract_abnormalities_async` 链)
- `_recover_anatomical_prefix`: prefix 含方法词/提示(心电图提示/彩超提示)不扩回(BAD 检查在
  prefix 本身)。junk-context 豁免: 枚举句(句首=编号/方法词+提示)内发现不滤(福建斑块/三尖瓣/牙)。
- `_parse_numbered_titles`: 方括号包裹标题("[CT 提示:冠脉钙斑]" 取 [] 内完整内容剥壳,
  马鞍山 [n] 式枚举); 未闭合 [ 跨行折行不产出(靠 LLM)。
- 尾端过滤(结论异常 postprocess): 名称以 `[` 开头(LLM 复制垃圾/拼接)丢弃; 疫苗/接种/幼儿/
  心脑血管疾病(健康建议文本)丢弃; "血管瘤"与"肝内高回声结节"并存时弃血管瘤(报告推测语);
  牙位前缀剥离("36牙楔状缺损"→"楔状缺损", 纯"智齿"=预防性拔除提示丢弃);
  `_METHOD_PROMPT_RE`(方法+提示+可带 放射科(CT) 修饰)前缀剥离, fill 与 LLM 名共用。
- 结论异常 store(`_store_abnormalities`)与指标黄区 cross_dup: 共享前 5 字变体判同实体;
  2026-09-09 口径: "超重/体重指数"结论仅当指标名含"身高体重指数"/"BMI"才拦
  ("体重指数"/"体质指数"均放行 —— 27 齐鲁超重与体重指数 26.37 并存、马鞍山肥胖与
  体质指数并存, 用户 2026-09-09 拍板); 其余条目做规范化互比(剥括号单位缩写/尾缀数字+
  号, 互含需被包含方 ≥3 字, 防"碳13尿素呼气试验阳性"被指标"尿素(BUN)"2 字核心误拦)。
- 福建第二(OCR 图片结论页): 结论页 2/3/5 号偶漏属 OCR 不稳定, 重 OCR(本 checkout 8001)
  可补全(响应字段是 `markdown` 不是 `text`); 补全后重建 `conclusion_text` 再提取。

### 环境/操作备忘(2026-09-05/06 新增)
- 23:56 批 13 份 PDF 在 `backend/storage/H001/batch/extracted/b4825d.../`; 广西/北京样本在
  仓库根 `体检报告样例/`(指标护栏直接引用)。两组 H003/H004 task/report id 20-27。
- 该批报告结论区/指标区数据多轮手工重跑落库; 再改提取规则后如需同步线上, 参照
  "删表格式行→pipeline 落库→删 interpretation 重投→验证"流程(worker 会用 backfill 自动补结论)。
- PaddleOCR(本 checkout 8001)HTTP 接口: POST /ocr, body `{"image_base64": ...}`(multipart 会 500),
  响应取 `markdown`。

### Skill 调用约定(写入即生效)
- 本仓库后续所有**生成逻辑改动/修复**, 动手前与过程中必须加载并遵守
  `karpathy-guidelines`(先想后做、最小改动、外科手术式修改、目标可验证)。
- 方案/需求存在歧义、或改动前想先被拷问一轮时, 调用 `grill-me`(grilling 会话)先行澄清,
  再进入实现。两个 skill 的调用要求与验证纪律同为工程约定, 后续 Agent 需遵守。

---

## H004 USER6 7 家报告表格适配(2026-09-07, table_extractor 块级解析器)

### 新增机制(全部在 table_extractor.py, 2026-09-07)
- `extract_column_table_rows` 新增两类**块级模板分派**(由表头词汇识别, 自动分派, 与旧列式共存):
  - **rev(齐鲁青岛型)**: 表头含 `异常标识`; dump 序 = [异常标识][单位][参考值][检查结果][项目名](名在块尾); ↑/↓ = 异常标志。装配 = 以名称为锚向上 ≤4 行。
  - **dual(厦门弘爱型)**: 表头含 `本次结果`+`上次结果`; dump 序 = [项目名][参考*][上次值][本次值]; **本次 = 块内最后一个值**; 异常标志 (↑/↓/*) 以括号挂值后。参考区可多行(叙述/带单位/单限/定性)。
  - 块模式产出行带 `__auth`; service.py 组装对 auth 名称**抑制行式/信号通道**(齐鲁 (X,"-") 海量、弘爱参考列当值)。
- `_range_with_unit()`: 参考值+单位同行("0.29-1.70mmol/L"/"≤5ng/ml"/"100-30010^9/L" 10^n 粘连)。**head 必须纯范围**——注释文本("TPSA在4-20ug/L")不吞(FPSA/TPSA 假黄回归教训)。
- `_merge_para_wrap()`: 综述折行假名(华西"甘油三\n酯:2.06" → 酯)消; `_merge_unclosed_square()`: 方括号跨行(">1000[阳性反应\n（+）]")。
- 值单元格规范化 `_norm_value_cell()`: 符号值(">1000[..]"→">1000")、半定量("+1"/"1+2.0")、"24.26,超重"。
- 反列序表(`_text_has_reversed_ref`: 表头"参考值"出现在"检查结果"**前**, 茂名型): 行式**值后 range 不向后消费**, ref 取自名称前一行; **常规 col 全程禁用**(否则 col 覆盖行式正确 ref 的回归: 肌酐 ref 曾配成尿酸 200-415)。
- `_NAME_RE` 名称字符集加空格 + 前缀后可空格("EBV 病毒核抗原IgG 抗体"); `_LATIN_PAREN_RE`("IgG(IgG/VCA)"续行并入)。
- dedup 同 key 时取 ref/flag 更全的行(华西表格行被综述无 ref 行顶掉的回归)。
- 信号通道(extract_abnormal_signals): 图标题("与既往我院体检结果对比图"等)起页跳过; 折行短名(≤1汉字≤4字)拒收("酯")。
- 黑名单补: 检查时间/检查医生/体检条码/页/共/手机/总检日期/查体号/出生日期/现病史/既往史/家族史/男/女/每/法/缘/白A 等。

### 七家验收结论(端到端重跑, interpretation 164-182)
- 21 厦门华西: 三列表(名称/值/↑↓/ref+单位同行)ref 全补齐; 酯/围/审核日期类垃圾清。
- 22 厦门弘爱: dual 解析 ~140 项, 11 项真实异常与检验科小结结论区逐条吻合(末值=本次铁律)。
- 23 山东省立: 尿胆原("1+2.0")+AST/ALT 恢复; 出生/查体/总检日期清; "乙型肝炎病毒核心抗体" 无标志超 ref 判黄 = **判定层口径问题**(用户口径"仅标志"), 不在提取层。
- 24 日照人民: 免疫三项恢复(>1000/>50, flag2); 尿隐血 +1; 缘/检查时间/降低( 清; 谷丙谷草/尿胆红素等"无标志黄" = 判定层。
- 25 茂名人民: 反列序 ref 全对(γ-谷氨酰 10-60 等); 假黄 44→6(全为超 ref 真项); 每/白A 清。
- 26 莆田: 体重指数 24.26(非 90); 小约为/审核日期清。残余: "阴性(-)乙肝表面抗体" 错位名、"维生素C -" 等 = signals/判定层。
- 27 齐鲁青岛: rev 解析 131 项; ↑ 异常 5 项全恢复(体重指数26.37/肌酸激酶237/EBV 双 IgG/乙肝表面抗体定量342.983); 体重 18.5 消失; 异常标识列值不再当 result。
- 残余判定层问题(不属 table_extractor, 见 interpretation triage/rules_engine): 21 酸碱度6/隐血阴性 误黄; 22 "总胆固醇 1.55/甘油三酯 3.37" 怪行黄; 24 尿胆红素/谷丙谷草/肌红蛋白 无标志黄; 茂名"仅标志判黄"口径待确认。
- 回归护栏 103 passed(含旧已知 ctni 失败已随空格名支持修复)。

### 2026-09-07 第二波(用户复查反馈修复)
- **pH/PH 纯字母名称**: `_NAME_RE` 加 `^(?:pH|PH)$` —— 尿检表 PH 行不识别 → 其 ref
  不被消费 → 被下一指标"名称前 fallback"吃掉(24 尿蛋白 ref 曾错配成 PH 4.500-8.000;
  26 维生素C 亦然)。另给值行后消化(j 循环)加**名称行即断**(莆田 pH 曾被当单位、
  "6.0" 当历史列、"4.5-8.0" 被误配给 维生素C)。
- **图表挖除 search 化**: `_HIST_CMP_TITLE_RE` 由整行精确匹配改为**行内含"历年对比/
  结果对比图/历次体检对比"即挖**(22 "总胆固醇历年对比" 图标题+轴刻度 1.55/3.37 垃圾源);
  signals skip_from 同步补"历年对比"。注意不要把"历史结果"(列式表头词)放进搜索词。
- **第二波修复(2026-09-07 深夜, 全部收口)**:
  - 21 尿沉渣 ref 断链根因 = `_text_has_reversed_ref` ±8 窗口跨表头误判(reverse 模式下
    range 分支 break → 比重 ref 不被消费 → 酸碱度/隐血 fallback 误配)。已改**表头单元格
    连续收集**判定(与列式表头同口径)。
  - 24 尿隐血"阴性"重复 = signals 配对向下取值跳过 "+1"(非标准数字)→ 抓参考"阴性";
    已让 `_pair_from_lines` 向下取值支持 `_norm_value_cell`(符号值/半定量)。
  - 26 "阴性(-)乙肝表面抗体" = 检验科小结红字配对残留; signals 输出拒名称含
    `(+)/(-)` 残留或定性前缀括号形态。
  - **判定层口径(用户 2026-09-07 确认)**: "仅标志判黄, 不做结果 vs 参考比较"——
    `interp_graph.py::run_rules` 删除无 flag 行的 ref 自动比较段; flag1(红字/异常词)
    复核兼容单限(ref_low=0 时仍比较上界, 防城港一 HBcAb 8.19>0.15); 护栏
    `test_gx_bj_indicator_guard.py::_judge_level` 同步(防漂移), 基线更新
    (桂林 +干化学酮体(报告*标记); 钦州二 -裸眼视力(无标志超限))。
  - 23 乙肝三项(flag=0 超 ref)端到端后将转绿。

### 2026-09-08 凌晨(用户口径确认轮)
- **红字不再作为判黄标志**(用户口径: 未发现仅红字标注的异常, 均有箭头/字母/提示文字/
  *; 华西/山东箭头在结果旁; 防城港一/桂林等有提示列): `extract_abnormal_signals` 删除
  red 通道。红行含 ↑↓/▲/提示文字仍由对应分支(arrow/flagtext)接管。
- 配套(防误删真异常): ①信号向上找名窗口保持 3 行(防城港一 HBcAb 提示列 ↑ 由**列式
  col**(名/值/单位/参考/提示 5 元组)承担 —— 其单位"PEI U/ml" 含空格, `_UNIT_RE` 允许
  内部一个空格); ②提示列字母 **A**(桂林尿干化学)入 `_FLAG_TEXT_RE/_FLAG_ABNORMAL_RE`;
  ③col 块收集/组装支持半定量/符号值(`_norm_value_cell`, 桂林酮体 "+1"); ④正常参考
  "阴性(-)"全/半角入 `_FLAG_NORMAL_RE`, 且 col 块内"名称 break"排除 FLAG_NORMAL/
  ABNORMAL 形态(参考/提示 cell 不再被当下一指标名而断块); ⑤贵港护栏基线移除"腰臀比"
  (仅红字, 用户确认可去)。
- 护栏 103 passed(36 guard: 防城港一 HBcAb/桂林 干化学酮体 经上述通道恢复判黄)。
- 27 齐鲁大便区(2026-09-08 已实施+用户复核修正): P13 表头→P14 内容跨页。视觉行 =
  名称|结果|参考(空)|单位 /HP|标志(↑/-), dump = [标志][/HP][结果][名](4-cell)。
  `_parse_rev_window` 组内含 "/HP" 时按镜检形态装配: **result = 紧邻名称上方行
  (脂肪滴 "0~1"), ↑ 仅设 flag=3 判黄(不当结果), 无参考范围**。验证: 脂肪滴 =
  0~1 / /HP / flag3; 粪便红细胞/白细胞不再错配(绿区镜检行暂缺, 端到端后按需补)。

### 2026-09-08(福建第二 OCR 结论链修复, 用户逐层验收)
- **OCR 文本不全根因**: `_EXAM_DETAIL_START_RE` 中"放射科/彩超室"等为**子串**匹配,
  "3. 放射科(CT)提示…/5. 放射科(骨密度)提示…" 编号条目行被误判为检查细节起点整段
  跳过 → 改科室词行首化 + **顶格编号行豁免**(编号行永不作为细节起点)。
- **异常结论与科普解释同行**: reflow 对"编号主条目行以冒号结尾"时, 后续解释/建议行
  独立成段(否则 LLM 把解释句拆成碎片条目)。
- **`_merge_wrapped_lines`**: 编号含冒号行 = 主条目**独立成行**; 后续独立
  "建议/请/必要时…"行并回该条(供 suggestion), **科普解释段不再拼入**(发 LLM 前
  keep_body_after_colon 会把无编号解释行丢弃)。
- **`_store_abnormalities` cross_dup 字符交集阈值 2→3**: "双髋关节骨质密度减少"(骨密度)
  vs 指标黄"低密度脂蛋白胆固醇"共享 {度,密} 被误杀(不同实体)。
- 验证: 福建第二(H003/24)结论区 = 13 条(含"双髋关节骨质密度减少", 无"骨量减少/
  骨显微结构改变/生物力学性能下降"碎片); 第 9 点含"建议复查中段尿…"完整。
  护栏 103 passed。结论侧离线仿真: `scripts/offline_conclusion_assemble.py <pdf>
  --report-id N --sync --db XXX [--hybrid]`(OCR 结论页报告需 --hybrid + `OCR_BASE_URL` = 本 checkout 的 8001)。

---

## 版面层(混合分层)改造 Phase 0/1(2026-09-07 起, 未接线)

**目标**: 表区驱动提取, 根治"提取跑到结论段/页眉/首页"与列错位; 格式无关适配未来医院。
**备份/回滚**: tag `pre-layout-refactor-2026-09-07`(改前全量状态); 每阶段 commit 独立。
**阶段(每步护栏全绿 + 离线仿真对照; 验证口径 = 自动对照为主 + 新报告目检)**:
- Phase 0(完成): `app/modules/report/layout.py` 骨架 —— `extract_visual_rows`(fitz
  dict line 粒度 = 单元格, 带 x/y/页)、`visual_text`、`coverage_ratio`; 护栏
  `tests/modules/report/test_layout_regions.py`: 30+ 样本无丢文本/行合法/图片型 skip。
- Phase 1(完成): `detect_table_regions`(规则 v1) —— 同 y(容差 8)聚簇逻辑行;
  表行 = ≥2 cell 且非字段标签行(体检编号:/姓名:…)/非图轴(历年对比/对比图)/非页脚
  (第/页/共N页 等 ≥半数); region = 连续表行段(gap ≤ max(3×行距中位, 90))。
  自动对照: DB 提取层带标志行(signal_flag>0, raw_text IS NULL)必须落在某 region
  (名称支持折行剥尾变体, 值按首 token 匹配) —— H004 7 家 + H003 5 家已验收报告全绿
  (95 passed)。
- Phase 2(未开始): 列语义 —— region 表头词 → 列角色(项目/结果/参考/单位/标志) +
  列 x 区间; 替换 extract_column_table_rows/块模式输入; 离线仿真 diff DB 0 差异 +
  103 护栏。
- Phase 3(未开始): 收敛 —— 表区驱动为主; signals 限表区; 行式仅作未识别表兜底;
  端到端一次 + 回滚判定(护栏样本非预期缺失或新报告垃圾>3 条即回滚)。
**观测事实**: fitz line 粒度 = 表格单元格; 同行多 cell y 差 ≤0.5、列 x0 高度规整
(华西 4 列固定 x≈42/256/340/470), 坐标聚类可行且稳定。

### Phase 2 进度(2026-09-07, 未接线, 进行中)
- `col_rows_via_layout(pdf)` 已实现 v4: region 表头角色序列(带表头 cell x0)与数据列
  (x0 聚簇容差 30, 保留全部列)对齐 —— 列数==角色数 1:1 zip; 列数多(华西 ↑ 窄列)
  按最近表头 x(容差 60)对齐, 对不上即列外标志。
- 统一覆盖: 常规列式(广西)、华西三列、弘爱双值(表头"本次结果"直接给结果列)、
  齐鲁反序(异常标识列) —— dual/rev 硬编码特判可被列语义取代。
- 召回现状(DB 提取层 flag 行, 8 家 66 条金标): 21/23 全过; 25 漏 1; 22/27/20 漏 2-5;
  24 漏 3; 26 漏 2。剩余 19 条漏项 = 具体数据形态(见下), 每项 1 轮可收敛。
- **剩余收敛清单**:
  1. 折行名称容差(24 EB 衣壳 "IgG(IgG/VCA)" 续行 y 差 15 > 链式容差 8; 免疫表行距
     35 —— 需"同 name 列近行补名"而非全局提容差);
  2. 22 乙肝两对半"结论行"(乙肝两对半结论|第2项阳性)与 HBsAb "阳性[234.27]" 免疫值;
  3. 23 体格/化验混合页列继承(体质指数/淋巴细胞绝对值等非 5 列表行);
  4. 27 化学发光区列 x 与主表偏移(52 vs 42)与 乙肝表面抗体定量 342.983;
  5. 26 谷草转氨酶/乙肝表面抗体(弱阳性)形态。
- 里程碑: 待召回全绿 + 与 DB 全行 diff 0 差异后再接线 service(替换
  extract_column_table_rows 调用点), 过 103 护栏 + 离线仿真。

### Phase 2 收敛进度(2026-09-07 晚, 8 家 DB 提取层金标 66 条)
- v6 达成: 齐鲁(李林青)/厦门两 家 0 漏(布局); 茂名(陈灿明)表头-数据列 x 倒挂 →
  "结果形态门"(result 大量非值形态)整体弃布局 → 交行式反列序兜底(原已验证全绿);
  莆田(蔡芳坤)经形态门恢复。剩余真实漏 6 条:
  - 日照(邵琳)3: EB 衣壳折行名(IgG(IgG/VCA) 续行 y 差 15, 需同列近行补名)、
    天门冬氨酸氨基转移酶（AST）名称尾缀、尿隐血 +1 行(形态?)
  - 德宏(毕建国)2: 总胆红素(TBIL) 19.2(H 行)、嗜碱性粒细胞数(BA#) 0.09(≤0.06 单限)
  - 山东省立(王国瑞)1: 体质指数(体格表, 3 词表头 vs 混列)
- 验收口径提醒: 布局-only 召回是中间指标; 最终以"组装(布局+行式+signals)"为准
  (茂名型弃布局后行式兜底即为全绿)。LAYOUT_DEBUG=1 环境变量可打印段 roles/cols。

### Phase 2 v7 收敛(2026-09-07 深夜): 8 家布局召回 → 仅余 1 条
- 修复: ①逻辑行链式容差 8→10(名称折行续行 y 差实测 9.1: 日照 邵琳 EB 衣壳/天门冬氨酸
  恢复); ②跨 region/跨页表头继承(德宏 毕建国 化验大表跨页无表头 → 恢复 0 漏);
  ③最近表头容差 60→90 + 窄标志(↑↓HL)不落非 flag 角色(山东 王国瑞 体质指数 值 x228
  vs 表头结果 x144 差 84 恢复, f3+ref)。
- 现状(布局-only 金标): 厦门华西(林建生)/厦门弘爱(戴伟平)/齐鲁青岛(李林青)/莆田九十五
  (蔡芳坤)/德宏州人民(毕建国) 0 漏; 山东省立(王国瑞) 0 漏; 日照人民(邵琳) 仅 尿隐血
  +1 1 条(所在 region 混入尿沉渣 172 列谱, 行式兜底已覆盖该行); 茂名人民(陈灿明)
  布局弃用→行式反列序兜底(预期)。→ 组装级(布局+行式+signals)8 家全绿。
- 下一步(收口): 接线 service 替换 extract_column_table_rows 调用 → 离线仿真 0 diff
  + 103 护栏 → 端到端一次(7 家重跑) + 回滚判定。

### Phase 2 接线结果与已知残留(2026-09-08 凌晨)
- 接线: service.process_task 与两护栏文件与 offline_indicator_assemble 统一走
  `col_rows_with_fallback(pdf, text)`(布局优先, 空/异常回退旧列式)。
- 护栏: 全量 259 passed(广西/北京 13 家改走布局后全绿); 步新宇(北京)基线移除
  "弃检" artifact(眼压 未检 不再判黄)。
- 防城港一修复: flag 判定收紧(参考文本 "0或偶见/HP" 含 H 曾误判 flag3)。
- 离线仿真 diff(8 家 DB vs 新组装)观察:
  * 利好: 大量旧垃圾消失(德宏"男年龄|25/手机号/镜检白细胞(SG-WBCJ)"、莆田
    "检查项目检查结果|13"、弘爱"危险分层低危|10"等);
  * **残留(待修)**: 跨 region 表头继承过宽 → 无表头叙述区(彩超/病史"器官|描述"
    两列文本)被当表组装出垃圾(华西 "前列腺|实质内查见点片状强回声"、莆田
    "13碳呼气试验值|0.12" 等)且 auth 抑制压掉行式正确行(华西 体重 76.0 被
    "76.0Kg" 顶掉)。**修法(下一轮)**: 把报告级"结果形态门"改成段级 —— 每段
    产出行 result 非法比例 >20% 即弃段(叙述区段弃用, 行式兜底恢复)。
  * 齐鲁(李林青)仅DB 33 含部分真行(体重|78 等)待复核(布局是否丢体格两列表)。

### Phase 2/3 收官(2026-09-08 上午)
- 段级形态门落地(替代报告级): 每段产出行 result 非法 >20% 弃段; 继承段(region 无表头)
  行无 ref/unit/flag 交行式(叙述区/测量区); 弃检/未检/放弃检查 前缀入名称黑名单。
- 全量护栏 259 passed(接线后布局通道全绿)。
- 端到端(8 家金标 process+解读, OCR): 黄红名单干净且与金标一致 ——
  厦门华西(林建生)2(甘油三酯/高密度脂蛋白; 酸碱度/隐血误黄消失)、
  厦门弘爱(戴伟平)12(11 真异常+乙肝两对半结论)、山东省立(王国瑞)13(尿胆原/体质指数等;
  核心抗体无标志黄消除)、日照人民(邵琳)6(免疫三项/尿隐血+1 等)、茂名人民(陈灿明)6(全真超ref)、
  莆田九十五(蔡芳坤)3(无体重指数90/维生素C垃圾)、齐鲁青岛(李林青)6(含脂肪滴 0~1)、
  德宏州人民(毕建国)8。
- 待办: H003 其它报告(滨州/潮州/福建/马鞍山/曹敬金等)DB 仍为接线前产物, 全量重 process
  另轮执行(~1h)。

### 滨州人民(董延广)结论段修复(2026-09-10)
- 根因: 结论定位锚点命中"体检综述"(实为 2025/2026 两年对比表) → conclusion_text 混入
  对比表 + 疫苗提示段; 真正结论段 = "▍异常指标解读"(L35-85, 1.超重 … 7.右肾囊肿)。
- 修复: report_profiles 加滨州档案(keywords=["滨州市人民医院"], anchor_only=True,
  extra_anchor=["异常指标解读"]); 指标挖除仍用通用+extra(综述表被挖)。
- 附带: _parse_numbered_titles 编号条目条件句("若/如/必要时/请"开头)不做标题
  (此前豁免 junk 过滤 → "若出现异常症状或原有症状加重"入结论); _SAFETY_NET_JUNK_RE
  加 若出现/原有症状/症状加重; "^建议$" 入名称黑名单(指标侧'建议|1.'杂项)。
- 结果: 结论区 = 右肾囊肿/左侧甲状腺结节/胆囊息肉样病变/超重(4 条, 无疫苗/综述/尾句);
  指标黄红 4 与基线一致; 护栏 263 passed。
- **教训**: 改动 worker/过滤代码后必须**重启 worker**再重投(否则旧代码继续跑, 表现为
  "改了没用"); 重启后核对 ps 启动时间。

### 滨州结论段换行修复(2026-09-10)
- 现象: conclusion_text 中 "2. ▲★尿酸偏高" 等编号条目被拼进上一段(如
  "(3)细嚼慢咽…不要进食。2. ▲★尿酸偏高尿酸为嘌呤代谢产物…").
- 根因: _locate_findings_sections 尾部"折行重排"环节的 item_head 正则编号后字符集
  不含 ▲△ → "n. ▲★名称" 未识别为条目标题(落普通正文拼入上一 buf)。
- 修复: item_head 与前置星标集补 ▲△(滨州"▲★"= 报告方异常标记);条目独立成行,
  正文/建议随后分行, 现有 short_title(编号短标题)规则随之生效。
- 验证: conclusion_text "2. ▲★尿酸偏高"/"3. ▲★总胆固醇偏高"/"4. ▲★血糖偏高"
  均独立成行; 结论区 4 条(右肾囊肿/左侧甲状腺结节/胆囊息肉样病变/超重)稳定;
  护栏 263 passed。

### 滨州页眉行滤除(2026-09-10)
- 现象: 结论段条目 6 与正文间混入跨页页眉"董延广·男·30   T114205"。
- 修复: _FINDINGS_STOP_SKIP_RE 加"姓名·性别·年龄(+编号)"无冒号页眉模式
  (^名2-8字·(男|女)·年龄(空格 编号)?$); 该行收集时跳过。
- 验证: conclusion_text 无页眉残留; 结论区 4 条稳定; 护栏 263 passed。

### H004 前 6 份端到端补齐与垃圾清理(2026-09-10)
- 补齐 H004 USER6 漏跑的 6 份(石坤/张亚/谢国宾/欧阳庆/庞海锋/步新宇)端到端;
  通用工具 `scripts/e2e_rerun_reports.py`(--db/--reports/--user/--mode)已入库并实战。
- 事故与修复: 钦州(欧阳庆)PDF 为纯扫描件(每页 1 图), 重跑时 OCR 服务异常
  (int(Tensor) 报错)导致指标被清成 0; 重启 OCR 后重跑恢复 119 行(与基线一致)。
  教训: 扫描件重跑前先确认 OCR 服务(本 checkout 8001)健康; process 需在 backend cwd 运行(否则 .env 未读,
  RabbitMQ 403 guest 认证)。
- 口径修正: 指标黄按 source='indicator' 对比;结论黄(source='conclusion')不计入
  (基线快照只含指标黄)。差异均为"旧假黄清除/旧漏黄恢复", 与护栏口径一致
  (钦州二 钾(K) 黄保留、裸眼视力去黄; 步新宇 弃检去黄; 桂林 干化学酮体恢复)。
- 垃圾清理(黑名单/清洗): 身份证/证件号码/N岁/体检次数 入名称黑名单;
  尿沉渣名称前缀参考("0-1个/LP颗粒管型"→"颗粒管型")落库前清洗;
  结论"需要进一步/进一步诊治"入 junk 强词 + safety-net 过滤;
  `_parse_numbered_titles` 数字直接接字母("50mm需要…")不再误判编号。
  重跑 1/2/3/5/6 后全部垃圾消失, 黄红稳定; 护栏 263 passed。

### 结论兜底标题/泛称回归修复(2026-09-10 深夜, 用户报告三例)
- 步新宇(北京医院): 编号兜底标题把"2、胸部CT 平扫：右肺尖间隔旁型肺气肿"解析成半截
  方法名"胸部CT"(方法词表不认"部位+CT"), "甲状腺B"/"腹部B"同; 真名由 LLM 承担。
  修: `_is_junk_fallback_title`(纯方法/检查名 + 科普定义句"X是指…")在
  `_parse_numbered_titles`/`_parse_summary_item_titles` 产出处过滤。
- 柳州(石坤): "肥胖"(LLM 科普句)与"轻度肥胖"(原文标题)并存 —— 泛称未去重。
  修: `_dedup_generic_findings`(程度前缀限定名保留, 去泛称; "肺结节"泛称在存在其它
  结节条目时去)挂 `_postprocess_extracted_items` 尾部。
- 钦州二(庞海锋): "肺结节"来自科普句, 真名"右肺中叶内侧段微小结节"已在 —— 同上去除。
- 测试变厚: 结论回归加 `forbidden_titles` 反向断言(步新宇样本禁 "胸部CT/甲状腺B/腹部B");
  `test_extraction_units.py` 加 4 条纯函数断言; 护栏 265 passed。
- **教训(为什么此前没测出)**: 结论回归的历史断言是"期望标题必须在"(包含式), 没有
  "不得多产"维度; 且 these 问题产生在重跑(新链)后的兜底/后处理层, 旧 DB 数据非护栏对象。
  今后新形态问题修复后, 同步补"禁产断言"(forbidden_titles/纯函数)。

### 结论多发现行/泛称/变体归一去重(2026-09-11 凌晨, 用户报告四例)
- 厦门华西(林建生): "龋齿"与"47龋齿"重复 → `_DENTAL_PREFIX_RE` 牙位前缀剥离
  ("47龋齿"→"龋齿")后归一去重; 组合整串("双肺散在小结节，多系炎性结节"各段已
  独立成条)丢弃。
- 厦门弘爱(戴伟平): 漏"龋齿/牙龈炎/牙结石/轻度脂肪肝/超重/左肺下叶少许纤维灶" ——
  编号行"4.残根；龋齿；牙龈炎；牙结石（+）"多发现只解析首词; 修: 编号行含分号 →
  整段作 title 交 `_expand_title_segments` 拆条(HL 只取首词会漏后项);
  `_expand_title_segments` 长串拆分阈值 ≥3→≥2(救"超重"); 尾"（+）"清洗。
- 山东省立(王国瑞): 漏"超重" —— 多发现连串"…增高，载脂蛋白B 增高，超重"中
  2 字短发现被长串拆分阈值(<3)丢弃(非体质指数去重, 见上条修复)。
- 日照人民(邵琳): ①"高血压/胸痛"= LLM 从科普句挖词; junk 检查源改用**未加工原文
  (raw_text)**, 加工文本裁句会丢建议词("如您有胸闷、胸痛…建议专科诊治");
  ②"ST 段轻度改变"空格变体 + "肝内血管瘤/可能""子宫肌瘤/可能"重复 → `_norm_key`
  (去空白/尾缀"可能")归一后去重。
- 防御沉淀: `test_extraction_units.py` +4 条纯函数断言(junk 源/组合整串/牙位/短发现
  展开); 护栏 265→267 passed。
- **答复口径(为何"之前没问题又出现")**: 结论条目主要由 LLM 提取, 每次端到端重跑都
  重新调用 LLM(非确定) → 集合会相对上次验收快照漂移(漏/多/写法变体); 规则层的
  解析盲区(编号行只取首词)与变体未归一使这些漂移放大。非某一次规则修改"引入"了这些
  条目, 是"重跑换 LLM 输出 + 盲区/变体"叠加。今后: 重跑后需对结论条目做一次 diff 抽查;
  新形态问题修复后同步补禁产/纯函数断言。

---

## 解读知识检索 = 确定性 planner/executor(2026-09-13, 消除 ReAct 退化循环)

**事实**: 「AI 解读」的知识检索步**不再**是模型自驱 ReAct 工具 agent。原
`build_interp_agent()`(`create_agent` + `SEARCH_SYSTEM_PROMPT` + `ConfirmSchema` +
`InterpKnowledgeMiddleware` + `ToolCallGuardMiddleware`)已全部**退役删除**。
现为纯函数两段式, 在新文件 `backend/app/ai/agents/knowledge_search.py`:
- `plan_knowledge_search(abnormal_indicators)` —— 按 `item_name` 去重, 每指标一条
  `SearchCall(indicator, query=item_name, fallback_queries)`, fallback 由
  `normalize_item_name`/`resolve_canonical`/去括号/斜杠拆分确定性生成(上限 3)。
- `execute_search_plan(hospital_id, plan)` —— 单趟执行: 首选空则依次试 fallback,
  命中即停; 全空/异常只记日志跳过, 绝不冒泡。
- `run_knowledge_search(...)` 为图节点入口; `agent_search_knowledge` 节点名保留
  (`interp_graph.py`), 仅节点体替换。

**原因(真实事故)**: H004 report 31 因 MedGo 单步生成退化循环(批量 tool-call 数组
无 `maxItems`)挂约 30min, OpenAI client 默认 `max_retries=2` 又把 600s 超时放大到
30min, 与 RabbitMQ `consumer_timeout` 撞车导致**消息丢失**、报告卡 `processing`。
且实测 agent 只是把指标名原样照抄为 query, 无选词增值。

**不要做的事**: ❌ 不要重新引入 `create_agent`/`with_structured_output` 让模型"看结果
再决定"的循环检索; 若将来确需模型选词, 只允许**单次结构化输出** planner(有
`max_tokens`/超时上限)。❌ 不要把该步的归属名改成 standard 名(须与
`indicator_judgment.item_name` 一致)。

**契约**: `knowledge_results` 形状不变(文档 key=int entry_id; KG key=`kg:<title>`),
`_generate_report`/`_merge_citations`/`persist(summary_refs)` 全依赖此形状。

**测试**: `backend/tests/test_interp_knowledge_search.py`(去重/fallback/空结果容错/
不调 LLM/refs 形状 12 条); `tests/test_toolcall_guard.py` 随守卫退役已删除;
`tests/ai/agents/test_interp_graph.py` 的 middleware 用例、`test_tools.py`/
`test_integration.py` 的 `INTERP_TOOLS` 断言已清理(`INTERP_TOOLS` 已删)。

**同日加固(两项, 针对 report 31 事故)**:
- `get_chat_model`(`app/ai/llm.py`)新增 `max_retries: int = 0` 默认关掉 openai SDK
  自动重试(原默认 2, 会把单次 600s 超时放大到 3×600=1800s=30min, 撞 RabbitMQ
  `consumer_timeout` 30min → 消息丢失)。快速失败交由应用层 retry 队列兜底。
- 解读看门狗 `app/core/interp_watchdog.py`(main.py startup 启动, 类似 batch_sweeper):
  周期(`INTERP_WATCHDOG_INTERVAL` 默认 300s)扫描各租户库 `report_interpretation`
  中 `status in (processing,pending)` 且 `created_at` 超过
  `INTERP_WATCHDOG_STALL_THRESHOLD`(默认 1800s)的行, **先置 pending 再重投**解读任务
  (必须先置: worker running-skip 会跳过 processing/completed); 已有 completed 解读的
  报告跳过; 内存 `_nudged` 去重防同窗口反复重投; 单次投递失败只记日志不中断。
  配置 `INTERP_WATCHDOG_ENABLED/INTERVAL/STALL_THRESHOLD`(`app/config.py`)。
  测试: `tests/test_interp_watchdog.py`(6 条) + `tests/ai/test_llm.py`(3 条) +
  `test_main_wiring.py::test_interp_watchdog_starts_on_startup_and_cancels_on_shutdown`。

---

## 异步 LLM 调用必须走 run_async, 不再用 asyncio.run(2026-09-14, report 33 事故)

**事实**: 所有运行时的 `asyncio.run(_guarded(model.ainvoke(...)))` 已改为
`app/ai/async_run.py::run_async(...)`(线程级常驻事件循环)。

**根因(report 33 直接失败)**: `langchain_openai` 对默认 async httpx client 做**进程级
缓存**(`_cached_async_httpx_client`, key=base_url/timeout) → 所有 `ChatOpenAI` 实例
共享同一个 `httpx.AsyncClient`。而 `asyncio.run()` 每次新建并关闭一个事件循环; 共享
client 连接池里的 transport 残留绑定在**已关闭**的循环上, 下一次 `asyncio.run` 去关闭
该连接时抛 `RuntimeError('Event loop is closed')` → 被包装为
`openai.APIConnectionError: Connection error.`。诊断关键: `id(m.root_async_client._client)`
在所有实例上相同; 纯 `openai.AsyncOpenAI` 无此问题(它每个实例自建 client)。

**触发条件**: 同一进程内 `generate_report`(生成) → `judge`(审核) → judge 判 fail →
`generate_report` 二次生成。第二次 `asyncio.run` 命中。judge 首次即通过的报告不会触发。

**改法**: `run_async` 用 `threading.local()` 为每个线程复用一个常驻 loop, 共享 client
始终绑定同一循环。改动点: `interp_graph._generate_report`、`judge_graph.run_judge`、
`interpretation/worker.py`(2× `_extract_abnormalities_async`)、`report/service.py`
(2× 解析)、`user_profile/service.py`(change overview)。`risk/gen_candidates_v2.py`
是开发脚本, 未改。

**不要做的事**: ❌ 不要在任何进程内再引入 `asyncio.run()` 调 LangChain 模型(会复发);
❌ 不要删/关 `run_async` 的常驻 loop(循环一关, 共享 client 又变孤儿)。

**测试**: `tests/ai/test_async_run.py`(同线程复用单 loop / 绑定资源跨调用存活 /
跨线程独立, 4 条); `tests/ai/agents/test_interp_graph.py` 增
`test_generate_report_does_not_use_asyncio_run` 与 `test_run_judge_does_not_use_asyncio_run`。
复现脚本(修复前 run2 必失败, 修复后 6/6 OK): 连续 `run_async(fresh_model.ainvoke)`。

---

## 结论侧 conclusion_text 重提取护栏(2026-09-15)

**新护栏**: `backend/tests/modules/report/test_conclusion_text_reguard.py` —— H003/H004 基线
覆盖的 26 份报告, 用当前代码从 PDF **确定性重提取结论段**(与生产 `_extract_conclusion_async`
锚点分支同构: `_extract_pdf_text(hybrid=False)` → profile `visual_sort` 时改按视觉坐标
重排 → `_strip_ocr_html` → `_locate_findings_sections`(profile extra_*) →
`_reflow_conclusion_lines`/`_reflow_table_conclusion`(+`_drop_review_block`) → 去空白
sha1[:16]), 与 `baselines/h{003,004}_baseline.json` 的 `conclusion_hash` 比对 —— 重跑前
即可发现结论切段/定位/reflow 代码回归。**终态(2026-09-15 收口)**: MATCH 24 严格 +
HYBRID 2(福建第二/欧阳庆: 扫描件离线链取不到段, 只锁空产物防漂移; 生产文本由三件套
DB 快照守)。**不覆盖总检异常条目名单(LLM 生成)** —— 仍由三件套 DB 快照
(`test_h003/h004_baseline_guard.py`)守。spec:
`docs/superpowers/specs/2026-09-15-conclusion-text-reguard-design.md`。

**2026-09-15 修复清单(全部带纯函数断言, 见 `test_extraction_units.py`)**:
- `_locate_findings_sections` 锚点前编号条目并入必须 `i == 0`(仅第一段; 钦州二重复行);
- 医生签名行(主检/总检/审核/终审)之后的锚点不再收集(潮州 `指标:DOB值`/德宏页脚
  `建议：` 伪锚点 → 附录整段/签名块漏入结论);
- `_drop_review_block` 块尾只认 ★ 行 + 块内"最后数据行之后的散文"识别为插入页割断的
  续行, 归位接回块前截断句(茂名"…运动员及器质性" | 综述页 | "心脏病者…" 重接;
  综述内容整块剥除);
- `_split_label_explanation` 保留源冒号字符(半角 `:` 不得改写全角);
- reflow"纯 ASCII 短行跳过"排除含 `/`、数字、`%` 的行(单位行 `mmol/L`/`U/L` 不再被吞);
- reflow 未闭合 `【`/`[` 标题续行合并(齐鲁"左侧下肢动脉中/层钙化】"断行);
- reflow `]`/`】` 结尾行后不并入续行(茂名综述数据行不得吞建议续行);
- 页眉跳过补裸年龄行(`43岁`)与医院名行(`山东大学齐鲁医院(青岛)`);
- `_pair_arrow_by_row` 跳过名称左侧单元格(马鞍山序号列 9/21/23 曾被当结果)。

**2026-09-16 第三轮修复(用户核对反馈; 均已重跑落库+基线重生成, 全量 451 passed)**:
- reflow 编号发现名(多名列表, 常超 28 字)与建议/解释行拆行
  (`_is_plain_finding_boundary` 编号条目 + `建议|请|注意` 就近判定) —— 潮州"健康建议"
  异常名未独立成行导致前端无法加粗;
- `_locate_findings_sections` 锚点前编号条目并入改为**对所有锚点**生效 + 跨段已收集集合
  去重, 并按编号归位(不再置顶) —— 福建第二"2. 彩超…"(印在"体检结论分析"前)插回 1 与 3
  之间; 钦州二重复行仍不复发(去重);
- `_is_findings_anchor` 双语标题回退(蔡超)必须"行首中文段之后**含拉丁字母**" ——
  否则纯中文正文行("建议：全口洁治…"/页脚"建议：")被误判锚点, 把分科列表/签名块收进
  结论(福建第二段 778→1374 字垃圾; 该修复同时兜住德宏页脚类);
- **VLM/扫描路径数值兜底**: 无任何标志且 result/ref 均数值且越界 → `signal_flag=2`
  (欧阳庆 MPV 7.30↓ 标志列被 OCR 整列丢失; 影响仅纯扫描件, 文本路径口径"仅标志判黄"不变);
- 结论段错误定位 `_extract_conclusion_async` 偶发返回 None 不更新(福建第二一次重跑;
  OCR 半崩溃态所致) → 重跑前必须 curl 验一页 OCR; 失败重跑一次。

**重跑扫描件纪律(再次强调)**: 8001 PaddleOCR 长时间运行后 `/ocr` 会 500
(`int(Tensor) is not supported in static graph mode`), **重启即恢复**。扫描件重跑前先
`curl` 测一页; 欧阳庆首次重跑即在崩溃态 → 报告被 wipe 后只恢复 5 行(重启+重跑后恢复
126 行+MPV/BMI 兜底标黄)。

**事故教训(2026-09-15, 同日两次)**: ①文件级 bisect 后"恢复工作区"曾把**未含当日修复的
旧 `/tmp/wt_*` 快照**拷回, 覆盖 `table_extractor.py` 两处修复, 表现为"测试顺序污染"假象;
②`e2e_rerun_reports.py` 在原文缺失时仍先 wipe → 报告产物被清空(崇左/茂名两次)。**已加固**:
脚本 reprocess 模式**预检原文存在**, 缺失则跳过该报告、不 wipe; bisect 快照恢复前核对
时间或恢复后 grep 修复注释; 快照用完即删。

---

## 第三批反馈修复(2026-09-16, H003-30 陈磊 / H004-40 陈镜霓)

**事实与根因(四修, 全量 468 passed 后重跑两份验收)**:

1. **`_EXAM_DETAIL_START_RE` 误吞发现清单(陈磊)**: `^(?:颈动脉|甲状腺|腹部|心脏|泌尿系|肝胆胰脾)彩超`
   把"异常检查结果"里的**分号并列清单行** `颈动脉彩超（查冠心病危险因子）；甲状腺彩超；B超（…）；…；脂肪肝`
   当细节段起点 → 其后 7 行(胆囊壁毛糙/腹腔胀气/膀胱内壁/甲状腺右叶结节/甲状腺左叶切除术后/
   心电图检查：1、窦性心律)被整段跳过。**修**: 该分支加负向断言 `(?!.*[；;])` —— 独立表头
   ("颈动脉彩超"/"心脏彩超（体检）")与潮州 ＋号并列清单仍照旧触发(26 份基线护栏零变化)。
2. **结论条目成对入库(陈磊 22=11×2)**: 并发 backfill 竞态(两 worker/watchdog 重投)+ `autoflush=False`
   使入口/循环内 COUNT 均看不到未提交行。**修**: `_store_abnormalities` 入口对
   `report_interpretation` 行 `SELECT … FOR UPDATE`(仅 mysql, sqlite 测试跳过) + 调用内 `_seen_names`
   同名去重。测试 `tests/modules/report/test_abnormality_store.py`(3 条 sqlite 内存库)。
3. **坐标路径结果形态(陈镜霓 白带常规)**: "Ⅲ级 ↑"(等级+箭头)/"++"(纯加号)不被认作结果 →
   整表被**段级形态门**丢弃(`_EXAM_DETAIL_START` 无关); 表头 "单 位"/"参 考 值" **字间空格**
   使 `cell_role` 返回 None → 单位/参考列整列丢弃(/HP 未接上)。**修**: `_GRADE_VAL_RE/
   _GRADE_ARROW_VAL_RE/_PLUS_ONLY_VAL_RE` 仅坐标路径启用(行式不启用: 其"值后文本序消费单位"
   会把下一行单位列错配给等级行); 形态门接受 等级/纯加号/单破折号; `cell_role` 空白归一。
   验收: `阴道清洁度（QJD）|Ⅲ级|无单位/参考|黄`、`白细胞（WBC1）|++|/HP`。
4. **建议归属校验补"头颈"**(陈磊重跑 LLM 把甲状腺建议贴给胆囊壁毛糙): `_BODY_ORGANS_RE` 加
   `头颈部?`; 报告 30 `--mode reinterpret` 后 胆囊壁毛糙 建议已清空。

**纪律提醒(再次)**: 改 worker 链代码(service.py 等)后必须 `bash /tmp/restart_workers2.sh`
再重跑(本轮 worker 启于改动前, 先重启 18:48 再重跑); 重跑扫描件前 curl 验 8001 OCR。
第三批两份验收结果: 陈磊 指标 158(仅 $\gamma$→γ 命名清理)、结论条目 5 条(旧 11 条含科普误抽);
陈镜霓 指标 125→133(非 raw 行口径)(新增视力/妇科查体/粪常规/病理结论行 + 阴道清洁度), 黄 4→5。

### 第三批 10 份纳入基线护栏(2026-09-16, 用户验收态冻结)

**样例**: `体检报告样例/0-第三批/`(东方医院/华山医院/六院金山/仁济医院/中医院 5 份 H003 +
安鹏/蔡超/陈镜霓/白玮衡/高帅 5 份 H004)。
**id 对应**: H003 27 鲍文祥(东方)/28 常逢龙(华山)/29 包雁飞(六院金山)/30 陈磊(仁济)/31 曹嘉冰(中医院);
H004 38 安鹏/39 蔡超/40 陈镜霓/41 白玮衡/43 高帅。
**基线**: gen 脚本 REPORT_IDS 已扩至 H003 18 份 / H004 18 份, JSON 重生成(旧条目零变化)。
**三层覆盖**:
- DB 三件套(test_h003/h004_baseline_guard.py, 按 JSON keys 自动参数化): 全部 10 份 ✓(108 passed)。
- 结论重提取(test_conclusion_text_reguard.py): MATCH +3(28/31/40, 严格 hash==基线);
  HYBRID/离线锁 +7(27/29/30/38 纯扫描、39 文本层损坏 force-ocr、41 白玮衡[生产段尾
  "本次体检结果汇总"块在签名后, 现行签名截断不含]、43 高帅[离线多姓名页眉行, 生产
  有 skip_lines])。**41/43 若未来重跑导致生产文本变化, 由 DB 快照报警走人工审**。
- 指标重提取(test_gx_bj_indicator_guard.py::TestSummBaselineYellow): +5(28/31/40/41/43,
  文本规则链可复现黄名单); 27/29/30/38(纯扫描 VLM)/39(force-ocr) 不在该层。

**同日修复(带纯函数断言)**: `_is_findings_anchor` 的"建议"类【】锚点判定过宽 ——
`"建议" in s` 会把"【肺结节】建议您胸外科/呼吸科定期复查随诊。"(发现+建议同行, 华山式
整类报告)判成锚点, 其下一行又是锚点时该段仅一行 → 被"空锚点段"规则整体丢弃(常逢龙
整条丢失; 重提取 hash DIFF)。**修法**: 只看 `【】` 内标题(`_t.group(1)`)是否含"建议",
不看整行 —— `【体检建议】`/`【医师建议】`(高帅)仍为锚点。修复后 28 转 MATCH。
测试: `test_extraction_units.py::test_inline_advice_bracket_not_anchor` +
`test_inline_advice_bracket_line_collected`。

---

## 新地区报告适配标准流程(v2, 2026-09-16 用户确认)

**背景**: 原管线(输入→调规则→跑护栏→重复→入基线)方向正确, 但护栏只覆盖"已入栏"报告,
且 junk/fill/新字段/扫描 VLM 路径是重提取护栏的盲区 → 补充为 7 步。**逐条为硬约定**:

1. **样本入库 + 形态体检**: PDF 放 `体检报告样例/<批次>/`; 登记 `conclusion_samples.py`
   (精修强断言 / `smoke=True` 弱断言); 形态体检 = 文本层 / 纯扫描 / 文本层损坏(force-ocr) /
   多栏 visual_sort / profile 命中 —— 决定走哪条链、是否需要 OCR, 防把"链选错"当"规则不准"调。
2. **先补保护再改规则**: 新报告先入回归集 —— 最小复现片段补 `test_extraction_units.py`
   (含"不得多产"断言), 整份入 `conclusion_samples.py`; 否则第 3-4 轮迭代中新报告自己无护栏。
3. **离线仿真 + 快照重放迭代**: 用 `offline_conclusion_assemble.py` /
   `offline_indicator_assemble.py` 秒级验证; LLM 侧先真调一次取快照
   (`ABNORMALITY_LLM_SNAPSHOT_SAVE=1 ABNORMALITY_LLM_SNAPSHOT_DIR=...`), 之后全部快照重放;
   规则差异数据化进 `report_profiles.py`(禁止按医院名写运行时分支)。
4. **跑全量护栏**: `pytest tests/modules/report/ tests/test_safety_net.py -q` —— 确认旧报告
   零漂移。若新规则与旧验收产物冲突: **说明冲突与影响面交用户决定**, 不私自更新旧基线。
5. **端到端一次(用户发令) + 用户前端验收**: 验收清单 = 字段(姓名/性别/年龄/医院/体检编码/
   年份)、指标黄红名单、结论条目、计数(红/黄/绿 = 指标逐条 + 总检建议整体 +1); 不符回第 3 步。
   纪律: 改 worker 链代码后必须 `bash /tmp/restart_workers2.sh` 并核对 pid 时间; 扫描件重跑前
   先 curl 验 OCR(本 checkout 8001)。
6. **冻结入基线**: gen 脚本 REPORT_IDS 扩号 → 重跑 gen → 人工审 `git diff` → 纳入三层护栏
   (DB 三件套 / 结论重提取 MATCH 或离线锁 / 指标重提取黄名单) → 提交 + 记录本文件。
7. **回滚点**: 改动前打 tag/commit; 回滚判据 = 护栏样本非预期缺失、或新报告垃圾条目 >3 即回滚。

**已知盲区(护栏验不出, 靠第 2/3 步兜底)**: junk 滤卡 / fill / 展开 / 跨线去重 / 新字段 /
扫描件 VLM 路径 —— 不在结论重提取与指标重提取护栏覆盖内, 由纯函数断言 + DB 快照 + 离线仿真守。

**第三批 10 份登记(2026-09-16)**: `conclusion_samples.py` 已补 10 条 —— 5 份文本件冒烟
(常逢龙 min_len=1800 / 曹嘉冰 500 / 陈镜霓 400 / 白玮衡 250 / 高帅 750), 5 份图片型或
文本层损坏(鲍文祥/包雁飞/陈磊/安鹏/蔡超, `ocr=True` 默认跳过; 精确 hash 与 DB 产物分别由
`test_conclusion_text_reguard.py` 与三件套快照守)。

### 第四批 5 份文本件适配与验收(2026-09-23)

**样例**: `体检报告样例/0-第四批/`(H004 45-49: 方轶涛/程永春/陈诚/华裕健/曹剑鸿;
另 5 份纯扫描未上传: 曾社健/薄楠/陈海波/陈淳铭/鲍海宽)。
**修复清单(全部带验证)**:
1. **个人信息**: `_name_ok`(机构/日期/地址词不算姓名, 精确词表+尾缀词) + 扫描所有
   "姓名"标签取首个合理值 + "尊敬的XXX先生/女士"兜底 + 坏名清除后交 LLM; 年龄 1-120
   校验 + 缺年龄触发 LLM 兜底; `_HEADER_WORD_RE` 扩充(出生日期/医院/中心…); 单位标签
   排除(体检日期/部门…)。案例: 陈诚"南通市第一人"→陈诚、华裕健"出生日期"→华裕健/年龄
   330→52、曹剑鸿 139→48。
2. **结论侧**: 台州档案 `visual_sort` + `extra_anchor=科室医生结论汇总`; 温州
   `visual_lines`(行级 y,x 重排, 两栏"发现|建议"配对) + 锚点容忍"冒号后括号注释"
   (`体检结论:（★…）` 含折行`（`结尾); 南通 `anchor_only` + `extra_anchor=健康指导建议`;
   郑州通知块 `extra_break`(感谢信跨页, 逐行 skip 抓不住后半段; 第二个"健康建议"锚点
   自动重启收集); 福州 `keep_post_sig`(签名后"体检结论汇总"段) + 结论页图片走 hybrid OCR;
   reflow 新增"标题（…）+解释"同格拆分(方轶涛 乙肝两对半独立成行)。
3. **指标侧**: `+-` 入定性值词表(尿胆原); 信号通道行内值支持半定量("2+" 与表格行去重);
   反序表"名称上方提示列文字/箭头"标志(必须限定 `_rev_ref`, 否则正序表整表假黄 ——
   护栏回归 8 份); `_skip_name` 去空白形态 + 词表补表头字段(台州"体 检 号"/"登记日期
   电话号码"垃圾行)。
4. **`match_profile` 白名单**: 新档案开关(`visual_lines`/`keep_post_sig`)必须登记进合并
   白名单, 否则档案声明不生效(福州 keep_post_sig 曾因此未生效)。
5. **程永春 `merge_wrapped` 已回滚**(2026-09-23 用户复验"效果不好, 回滚上一轮";
   保留 `visual_lines` 配对修复)。教训: 结论条目/建议的换行形态由用户前端展示习惯决定,
   不要仅按"片段合并"直觉改。
**护栏冻结**: H004 基线扩至 23 份(45-49); 结论重提取 31 MATCH + 10 HYBRID(45 方轶涛
结论页图片→离线锁); 指标重提取 +4(45/46/47/48), **49 排除**(其"乙肝表面抗体"提示列
文字 f=1 无参考: 护栏 `_judge_level` 镜像判黄、生产 run_rules 口径判绿 → 差 1 条;
由 DB 三件套与结论护栏覆盖)。`_BASELINE_CASES` 的 B3/B4 目录常量分开(第三批/第四批)。
**待办**: ①5 份纯扫描未上传适配; ②`unit_name=医院(体检机构)` + `exam_no` + `exam_year`
落库方案(口径已定)未实施。

### "其他指标"垃圾条目清理与过滤规则(2026-09-23, 曹嘉冰/常逢龙/陈田)

**用户反馈三份报告前端"其他指标"含垃圾行** → 提取侧规则 + 一次性数据清理:
- **行式垃圾单元格 `_junk_row_cell`**(table_extractor): 定性碎片("阳性( DOB")/
  名称尾随数值("糖化血红蛋白A1c(NGSP) 5.60"、"胃泌素-17（G-17）(餐前) 2.53")/
  重复名("清晰透明清晰透明"/"淡黄色淡黄色")/合并多条目行(单位含"，名称: 数值" →
  "蛋白A: 0.80 , 总胆固醇: 5.33")/查体部位+正常结果(头面部/裂隙灯/甲状腺)。
- **`_SKIP_NAMES`** += 吸烟史|饮酒史|最大|最小; **`_SKIP_SUB_RE`** += 院区|总\s*院
  (常逢龙"总 院：021-…/浦东院区：021-…"医院联系信息行)。
- **重复字装饰行不并名**: `_REPEAT_CHAR_JUNK_RE`("上上上上上" 曾并进"淋巴细胞百分比"
  成"淋巴细胞百分比上上上…") —— `_merge_wrapped_names` + 列式 name 单元格 +
  `_skip_name` 三处都拦。
- **空结果信号行不入库**(陈田"甲状腺抗体异常|NULL"); **患者姓名同名行清理**
  (曹嘉冰封面"曹嘉冰/44") —— process_task 与 offline_indicator_assemble 同构。
- **数据清理**: 三份共 20 行(连带 judgments)删除 + 重算计数。注意: 存量
  `green_count` 本身有偏差(与 judgments 实际数不符), 重算后为真实值。
  基线不受影响(垃圾均为绿区, 不在黄红名单/结论快照内)。
- 测试: `test_extraction_units.py::test_junk_row_cell_filters`。

### 第四批 5 份扫描件适配与验收(2026-09-25)

**样例**: `体检报告样例/0-第四批/`(H004 50 薄楠/51 鲍海宽/52 曾社健/55 陈淳铭/56 陈海波,
纯扫描; 56 陈海波为**单表双栏**、52 曾社健/55 陈淳铭为**单表双栏或 7 列表**)。
**修复清单(全部实测+单测)**:
1. **VLM 表格解析重构**(根因级):
   - **逐表解析**: `_parse_markdown_table` 按 `<table>` 块拆分(`_rows_to_indicators`) ——
     旧实现把整页所有 `<tr>` 拉平成一个表, 表头/列映射跨表串用(双栏错乱/假黄根因);
   - **单表双栏周期拆分**: 表头角色序列按最小周期重复(陈海波"分析项目|结果|参考值|单位"×2)
     → 每行拆子行分别解析(p11 28→57 项, 预期异常全恢复);
   - **历年对比表整表跳过**: `_is_history_table`(表头/次行含 ≥2 日期列); 文本路径
     `_HIST_CMP_TITLE_RE` 补"前次体检结果对比"等变体(陈海波 p22 历史值曾被当本次指标);
   - **标志列/箭头归一**: `_norm_flag_cell`(剥 `$`/空格 + LaTeX 箭头→↑) + 结果右侧
     尾箭头先剥 `$`(陈海波); 标志扫描**限定已映射列**(历史列"阳性"不再当标志);
   - **"本次小结"行兜底**: OCR 丢提示列时从表尾小结(名称+阳性/弱阳性)补 f=3(薄楠尿胆红素/尿胆原);
   - **数值越界兜底时机**: 统一在 ref 拆分后执行 —— 拆分前 `_as_float("<2.7")` 会把**单侧
     上限当下限**误判(陈海波甘胆酸/总胆固醇等假黄、陈淳铭肝纤维化指数);
   - **分级定性参考清标志**(用户口径): "阴性:<0.9 可疑…阳性…" + 数值落阴性带 → 清误读 ↑
     (薄楠 EB病毒早期抗原IgM抗体 0.05), 保留行; 真阳性(196.2≥20)标志保留。
2. **OCR 服务崩溃是 52/55/56 首轮"空产物/空指标"主因**: 8001 故障(int(Tensor))→
   **重启即恢复**, 重跑后 55/56 正常解析、52 指标恢复。扫描件重跑前先 curl 验 OCR。
3. **名称/分类(用户口径)**:
   - "微白蛋白"与"尿微量白蛋白"是**两项不同指标**(微白蛋白=尿检项), 曾加别名合并 →
     **已回滚**;
   - `classify`: 微白蛋白→尿常规(原误入肝功能"白蛋白"); 尿微量白蛋白/尿转铁蛋白/
     尿免疫球蛋白G/尿α1微球蛋白/甲状腺球蛋白抗体→**免疫检测**(原误入肝功能/尿常规);
     真肝功项(白蛋白/球蛋白/总蛋白/前白蛋白/胆红素)不变。**分类读取时计算, 改完须重启后端**。
4. **孤儿判定清理(2026-09-25)**: `indicator_judgment` 存在指向已删除 interpretation 的
   悬挂行(H001 69/H003 2084/H004 7159; 历次 rerun wipe 竞态遗留, UI 不可见) →
   已全量清理为 0。清理语句须 `USE db;`(跨库多表 DELETE 不选库会 1046)。
**护栏冻结**: H004 基线扩至 28 份(+50/51/52/55/56); 结论重提取 +5 **离线锁**(纯扫描,
离线 hybrid=False 无产物); 指标重提取**不纳入**(纯扫描规则链无产物, 由 DB 三件套守)。
**遗留待办**: ①`unit_name=医院(体检机构)` + `exam_no` + `exam_year` 落库方案(口径已定)
未实施; ②45/49 结论条目偏少(LLM 抽取, 用户已验收, 如需更全可再调)。

### 第五批 5 份文本件适配与验收(2026-09-28, 用户第二轮反馈)

**样例/落库**: `体检报告样例/0-第五批/`; H004 57 付立光/58 毕惠丽/59 蔡丰泽/60 蔡明/61 曾繁鲁
(另 62-66 厚柏刚/单吉飞/生明月/董涛/陈春光 待上传)。生产走 hybrid; 60 蔡明需宽条图页 OCR。
**修复清单(全部离线仿真 + 端到端 4 轮 diff 验证, 黄红 0 变化)**:

1. **结论段移除静默失败(根因级)**: `text.replace(section, "")` 在结论段收集**跳过页眉/
   断点行**时(收集文本非连续子串)失配 —— 26 基线中 31 处, 结论条目/科普混入指标线
   (曾繁鲁"酯(TG)"/池州"您的 骨密 度正常"/高帅"运动处方咨询电话")。改为
   `_remove_findings_section` 行序子序列删除(候选起点须匹配全部段内行, 取跨度最小; 回退 replace)。
2. **坐标列 `_col_index` 回归修正**: 空标志列时"最近表头中心"把参考范围数据误配给 flag 列
   (付立光 FPSA/TPSA 假黄)→ 单调映射; 但无条件单调会把序号列吃成名称(山东"14"/"20"假黄)
   → **仅"最近表头是 flag 且单元格长文本"时启用单调**, 其余保持最近表头。
3. **数值+中文定性后缀拆分 `_split_qual_tail`**: "130 阳性"/"0.00732 阳性"(乙肝两对半
   定量表)整串当结果 → 与行式"130"重复两条且单位错成"阳性"; 拆为数值+定性(阳性→标志),
   行式 `_VALUE_RE` 尾随定性词不作单位。
4. **指标名称折行续行([HR] 前缀)**: `_NAME_RE` 允许 `[XXX]` 前缀、`_NAME_CONT_MIXED_RE`
   ("测定(HBsAg)"/"移酶(AST)")、纯拉丁括号续行("(NEUT%)")并入名称; 组装层**名称后缀补全**
   (列式截断名是行式全名前缀且结果相同时采用全名); 信号通道碎片名**改挂全名**
   (`_retarget_signal_name`, 曾繁鲁黄区不再拆两条)。列式/信号碎片均由这三处收口。
5. **结论 reflow**: ①item_head"数字+空格+汉字"且上段未句末 → 折行续行(曾繁鲁"连续观察"/
   "3 年无改变"); ②段头括号由"完全无闭合"放宽为**不配平**(付立光【检查:结果】多项列表
   折行中段); ③`_split_label_explanation` 子项编号标签与数值起始 rest 不拆;
   ④长编号标题后短中文标签行拆行(蔡明"…3×3 mm"/"疾病解释"); ⑤历年对比日期标签
   ("2025-05-21 检查")并入下一行(单独成行会被前端加粗; 孤立日期丢弃)。
6. **结论采集「未见异常」跳行误杀**: 上一原始行有未闭合 `【` 时本行是括号清单折行续行,
   不跳(付立光"动脉硬化】【心脏彩超…未见明显异常"整段曾缺失)。
7. **档案新增**(report_profiles): ①盛京医院 `visual_sort=True`(毕惠丽第 3 页文本流乱序:
   正文 9-13 点排在免责声明/签名块**之后**, 线性收集在签名处截断 → 视觉重排后 9-13 点先于
   免责声明, 断点仍生效) + 问候语锚点 + 免责声明断点; ②丹东 `anchor_only`(只取健康指导段);
   ③付立光院 `extra_break=疾病解析`; ④三亚"专家建议及健康指导"锚点; ⑤江西中医大附院
   **`ocr_content_pages=True`**(文字标题+宽条图片正文混合型, `_extract_pdf_text(hybrid,
   ocr_content=True)` 对"文本<600 且 ≥3 条宽条内容图"的页补 OCR; process_task 在档案命中后
   重提取; 离线脚本 hybrid 模式同构)。**新档案开关必须登记 match_profile 白名单**(曾踩)。
8. **指标垃圾收口**: `_LATIN_PAREN_RE` 前缀须含拉丁字母(付立光"0.00(-)"不再被当名称续行);
   `_skip_name`/`_SKIP_SUB_RE` 补: 流水号/联系电话/审核时间/报告时间/检测结果/病案号/检查号/
   病人信息/申请科室/申请医生; `_SKIP_NAMES` 补 `小结$|体检$|个/[\wμµ]+`(尿沉渣单位行不并名);
   行内模式拒绝"值后接范围分隔符+数字"(性激素参考"期:4.54-22.51…"不再产"期|4.54"垃圾);
   坐标名称列跳过机构抬头整行(`_HOSPITAL_HEADER_RE`, 蔡明"江西中医药大学附属医院+指标名")。
9. **分类(毕惠丽)**: `classify` 支持单位消歧 —— 裸名"红细胞/白细胞"按单位(尿 `/μl //HP`
   vs 血 `10^12/L`)归组; "白细胞酯酶"拉黑出血常规; `group_indicators` 报告级尿检上下文
   (有尿检强项、无粪检项时裸"隐血"归尿常规)。**改 classify/group_indicators 须重启后端**。

**护栏冻结(三层)**: H004 基线扩至 **33 份**(+57/58/59/60/61, 旧条目零变化);
结论重提取 **35 MATCH + 16 HYBRID**(57/58/59/61 MATCH; 60 蔡明图片详情→离线锁
`143cfedffe8fae19`); 指标重提取 +4(57/58/60/61), **59 蔡丰泽排除**(碳14呼气试验阳性/
维生素C± f=1 无参考: 护栏 `_judge_level` 镜像判黄、生产 run_rules 判绿 → 差 2, 与 49 同类;
由 DB 三件套与结论护栏覆盖)。
**测试**: `test_extraction_units.py` 本轮 +14 条(定性后缀/机构抬头/日期合并/子序列删除/
单位消歧/续行合并/档案开关等); 全量护栏 `611 passed / 31 skipped`。
**遗留**: ①62-66 五份待上传适配; ②蔡明 `ocr_content_pages` 宽条图页 OCR 约 11 页(~90s/份);
③59 碳14/维生素C 生产判绿 vs 护栏镜像判黄的口径差(待用户明确是否弱阳性判黄)。

### 第五批后五份离线预检(2026-09-28, 62-66 待上传)

**结论侧离线核验通过**(未上传, 无 DB): 厚柏刚(盛京)1-17 点(visual_sort)/单吉飞"指导与
建议"/生明月"本次健康建议"/董涛 1-13 点/**陈春光(福州省立)hybrid OCR 可提取**(与方轶涛同款
图片结论页, 605 字)。**董涛指标侧预修复**: 宜昌市中心人民医院体格表/尿常规**页内文本流乱序**
(值+参考挤到上一名称前), 信号通道按文本序向上找名 → "舒张压|26.16"/"总胆固醇|2.09"错配;
修法: `_pair_arrow_by_row(..., result_hint=...)` 对"值+箭头"同格行**按同排(y)取名**,
`_TRAILING_ARROW_VAL_RE` 支持半定量值+箭头("+-  ↑" → 尿潜血/尿蛋白)。全量护栏 612 passed。
(单吉飞遗留: "【总检汇总】"11 条 vs "【指导与建议】"7 条, 缺 4 条无建议项, 待用户口径。)

### 第五批 5 份(62-66)适配与验收(2026-09-28, 用户第三轮反馈)

**落库**: H004 62 陈春光/63 单吉飞/64 董涛/65 厚柏刚/66 生明月。**修复清单(端到端 3 轮, 黄红 0 变化)**:
1. **结论跨页续段**(单吉飞): "终审医师/初审医师"签名块后仍有续页条目(肾小球滤过率增高/
   HDL-C/脉搏减慢/腰臀围比值偏低) → **extra_skip 优先于通用断点**(`_locate_findings_sections`
   检查顺序调整)+ 该院档案 extra_skip(签名/地址/页码碎片); 段尾由"检查项目"BREAK_RE2 收口。
2. **结论停止位**: ①生明月 "检查医生" 入通用 BREAK(建议段尾接分科明细); ②董涛 复合抬头
   "医院·学院/大学"页眉入 SKIP_RE3; ③患者姓名页眉行(2-4字+"姓名:"相邻)跳过(单吉飞/董涛
   结论尾混入姓名)。
3. **reflow 编号正文折行**(厚柏刚): 编号行**长且含全角句读**(≥35字且 ，：)时不再触发
   发现/建议边界拆分("5、EB病毒…疾病等有"/"相关性，请结合临床…"); 短标题(蔡明"7. 胆囊
   息肉，约3×3mm")与多名列表(潮州/广西人民/贵港)仍拆。
4. **段内去重短续行**(厚柏刚"诊。"): 重复行去重豁免 ≤3 字短行(两条"…就诊。"的第二条曾被吞);
   **副作用**: 弘爱(22)离线重提取会补回"泌尿外科门诊就[诊。]"—— 基线冲突待用户确认(见下)。
5. **单位补全**: `_REF_UNIT_SUFFIX_RE` 剥单位后去尾空格("20.00～50.00 %" 生明月/单吉飞);
   ref 后的"单位样"行按**下一行是否为值**判断是单位("%")还是下一指标名("*pH"/崇左);
   `_NAME_RE` 斜杠别名尾段排除 "min"(ml/min 被当名称致单位丢)。
6. **分类**(读取时计算, 改完重启后端): 血常规+="中性分叶"; 肾功能+="肾小球过滤"/"补体C1q";
   心肌酶谱+="门冬氨酸"(肝功能先命中"天门冬/天冬氨酸", 不受影响)。
7. **坐标形态门放宽**(董涛): 段级结果形态门只把**长叙述(≥8字)**计坏, 短定性/颜色值
   ("未提示/normal/清亮/+-")不再触发整段弃用 —— 血脂4行(低/高密度/小而密/非高密度脂蛋白
   胆固醇)与尿常规同段, 曾被整段丢弃。
8. **分科叙述垃圾**: `_EXAM_PART_JUNK_RE` 补五官/查体部位(听力/扁桃体/嗅觉/咽喉/心脏听诊/
   外阴/宫颈…)+`_NORMAL_RESULT_RE` 补(无肿大/未闻及异常/通畅…); 结果为字段标签残片
   ("检查者凌延昭")判垃圾; 名称含日期残片判垃圾; `结果提示` 入含词黑名单;
   坐标结果前缀装饰标记(◆◇■□●○)剥除(单吉飞"◆19.3 ↓"重复行)。

**护栏**: 全量 `624 passed / 31 skipped`, 唯一红: `test_conclusion_text_strict[key17]`
(弘爱 22 —— 去重改进使离线重提取补回被吞的"诊。"; **待用户确认后重跑 22 + 重生成基线**,
不私自更新)。
**遗留**: ①弘爱基线冲突(上); ②62-66 待纳入三层基线(用户验收后)。

### 62-66 第四轮反馈 + 弘爱(22)基线冲突收口(2026-09-28/10-03)

**修复**:
1. **收费/状态清单不产指标**(陈春光 62): `_SKIP_SUB_RE` 补 `已做|未做|收费项目` ——
   该院"收费项目汇总"(项目名+项目状态"已做")曾以"XX已做|序号"成批入库(25 行)。
2. **AST/ALT 比值分类**(陈春光): 肝功能 includes 补 `AST/ALT|AST:ALT`。
3. **风险分层参考不落 ref**(董涛 64 低密度脂蛋白胆固醇): ref 文本含"人群/风险等级/
   危险分层"时不解析(分层目标值非本行通用参考, 用户口径"宁可不显示")。
4. **rev/dual 块表区信号整体跳过**(弘爱 22): `extract_abnormal_signals` 用
   `_block_windows(all_lines)` 过滤块区信号 —— 否则"上次结果"列值(带箭头)与"本次"
   双双成信号(总胆固醇 5.73/5.80 双黄、AG 9.2 错配假黄)。
5. **箭头带括号值**(弘爱"7.9(↓)"): `_TRAILING_ARROW_VAL_RE` 允许 `(↑/↓)` 后缀 ——
   信号行自身值可取, 不再向下撞到上一列"上次结果"。
6. **组合查体部位 + 字段标签结果**(弘爱重跑暴露): `_EXAM_PART_JUNK_RE` 支持
   "、"组合(皮肤、浅表淋巴结/胸廓、肺部); 结果为"检查结果/检查项目"等字段标签判垃圾。

**弘爱(22)基线冲突收口(用户拍板"按建议来")**: 原文缺失(storage 已清)→ 以护栏样本
回填原路径后重跑; 收口后 22 = 结论 554 字(补回"诊。")/ 指标 137 行 / 黄红 12
(清掉 13 条旧垃圾 + 5 条"上次结果"假黄; 体格项/身高体重血压等按现规则恢复入库)。
`gen_h004_baseline` 重生成(22 条目更新), 三层护栏全绿。
**护栏**: 全量 `628 passed / 31 skipped`。AGENTS 与基线同步。

### 第五批 10 份全量验收冻结(2026-10-03, 用户"可以纳入基线护栏")

**护栏冻结(三层, 62-66 并入)**:
- **DB 三件套**: `gen_h004_baseline.REPORT_IDS` 扩至 **38 份**(57-66 全入); 基线重生成,
  旧条目零变化(22 弘爱条目随"按建议来"重跑更新)。
- **结论重提取**: 63 单吉飞/64 董涛/65 厚柏刚/66 生明月 **MATCH**; 62 陈春光
  (结论页图片, 生产 hybrid) **HYBRID 离线锁**(空产物 `da39a3ee5e6b4b0d`)。22 弘爱
  重新 MATCH(基线更新后)。
- **指标重提取**: +62 陈春光/63 单吉飞/64 董涛/65 厚柏刚; **66 生明月排除**
  ("维生素C ±" f=1 无参考: 护栏 `_judge_level` 镜像判黄 vs 生产 run_rules 判绿,
  与 49/59 同类; 由 DB 三件套与结论护栏覆盖)。
- **样本库**: `conclusion_samples.py` 10 份(57-66)已登记。
- **全量护栏**: `652 passed / 32 skipped`(tests/modules/report + tests/test_safety_net)。

**第五批收官**: 10 份 = 57 付立光/58 毕惠丽/59 蔡丰泽/60 蔡明/61 曾繁鲁/62 陈春光/
63 单吉飞/64 董涛/65 厚柏刚/66 生明月 全部验收并冻结。遗留: ①单吉飞"总检汇总"11 条
vs "指导与建议"7 条(4 条无建议项已随跨页修复进入结论, 不再遗留); ②`unit_name=医院
(体检机构)+exam_no+exam_year` 落库方案未实施; ③59/66 "维生素C ±" 生产判绿 vs 护栏
镜像判黄的口径差(待用户明确是否弱阳性判黄)。

### 第六批前 5 份验收冻结(2026-10-07, H004 67-71 用户确认"加入基线护栏")

**样例**: `体检报告样例/0-第6批/`。id: 67 安周(吉林大学中日联谊医院)/68 白炜(海口, 医院名未识别)/
69 翟泽民(**纯扫描**, 九江边检站)/70 蔡克田(海南慈铭奥亚)/71 蔡国炜(儋州美年)。

**护栏冻结(三层)**:
- **DB 三件套**: `gen_h004_baseline.REPORT_IDS` 扩至 **43 份**(+67-71); 基线重生成,
  旧条目零变化(JSON 仅 +418 行)。`test_h004_baseline_guard.py` 129 passed。
- **结论重提取**: 67 安周/68 白炜/70 蔡克田/71 蔡国炜 **MATCH**; 69 翟泽民纯扫描
  **HYBRID 离线锁**(空产物 `da39a3ee5e6b4b0d`; 生产文本由三件套守)。
- **指标重提取**: +67/68/70/71(文本规则链可复现黄名单); **69 排除**(纯扫描走 VLM 指标,
  规则链无产物; 由 DB 三件套守)。
- **样本库**: `conclusion_samples.py` 批次6 10 份已登记(8 冒烟 + 2 ocr)。
- **全量护栏**: `698 passed / 35 skipped`(tests/modules/report + tests/test_safety_net)。

**本轮修复(用户 2026-10-06~07 逐份反馈; 全部有断言/验证)**:
1. **e2e 工具双投递(根因级)**: `process_task` 末尾本就 publish interpretation,
   `e2e_rerun_reports.py` reprocess 又补投 → 每报告两条消息、两 worker 并发跑两版解读
   (旧版被删成**孤儿判定**, H004 累计 4031 条已一次性清理)。工具已改为 reprocess 不补投
   (reinterpret 仍补)。另清理: 结论占位行成对入库(双 backfill)去重、无判定残留行;
   每报告仅 1 条解读。
2. **结论侧**:
   - 段内去重豁免**建议/提示句**(安周第 13 条"建议：定期复查。"与第 11 条同文曾被吞);
   - **行尾孤立"0"标记剥离**(白炜 PDF 文本层每段尾渲染"0"字形, OCR 证实印刷无; 前置为
     汉字/句号/括号才剥, "0类"/"1.0" 保护; 独立"0"行跳过);
   - 蔡克田/蔡国炜档案: `anchor_only`(锚点"一般健康问题") + `extra_break`
     ["医学科普","分项报告"](科普区不提取);
   - `_EXAM_DETAIL_START_RE` 部位彩超行加 `(?![:：]\S)` 守卫("心脏彩超:三尖瓣少量反流"
     数据行曾被当细节段起点, 吞发现+建议);
   - reflow: 断词续行("10m"+"m"=10mm)、纯值片段("0.1--0.6)"/"00)")与括号续行并入、
     堆叠多发现各自成行(值+ref 行逐行)、建议性正文段折行合并。
3. **指标侧**:
   - **反序误报降级**(白炜): 文本层表头 dump 顺序被排版打乱, `_text_has_reversed_ref` 误报
     反序 → `_above_flag` 把上一行尾部箭头记到下一指标(成片假黄)。新增
     `_data_order_clearly_normal`(数据行序明确正序: normal≥20 且 >2×rev)**仅作降级**;
     茂名 16/7、齐鲁/弘爱 block 表不受影响;
   - `_norm_value_cell` 支持**双符号括号值**("10(+-)"), 修复箭头配对滑到参考"阴性"假黄;
   - **`_URGENT_RE` "危险"复合词排除**("危险因素/危险分层"等不是急症语义), `_mark_urgent`
     同时清除 LLM 此类误判; LLM 提示词同步限定。蔡克田"双侧颈动脉分叉内-中膜增厚"建议含
     "危险因素"曾被判红区(用户反馈), 修复后 reinterpret 转 yellow。
4. **结论条目回填(LLM/safety-net)**:
   - **汉字序号必须带分隔符/空白**(同日回归修复): "一般健康问题"/"二氧化碳结合力"曾被
     "一/二"剥头成"般健康问题"/"氧化碳…"垃圾回填;
   - **"N、检查名:发现"标题取发现侧**("1、胸部CT平扫:双肺多发结节"→"双肺多发结节";
     "12、牙周:牙结石"→"牙结石"; 冒号前有发现/方向词或冒号后是建议句仍取冒号前);
   - **否定语境过滤** `_negated_occurrence`("无上皮内病变或恶性病变(NILM)"不再拆出
     "上皮内病变/恶性病变"假条目; "无痛性血尿"复合形容词不误杀)。
**已知小残留**: 68 结论条目含"甲状腺实质弥漫性改变"+图注变体"…改变图像"(近似重复);
67 条目"右肺上叶后段"为长名截断(原文含中叶/下舌段/前内基底段索条影)。

### 第六批后 5 份验收与基线冻结(2026-10-08, H004 72-76, 用户"验收完毕")

**样例**: `体检报告样例/0-第6批/`。id: 72 蔡道龙(保山支队)/73 蔡镇宇(**纯扫描**, 舟山中医院)/
74 陈东海(八所边检, **双文本层**)/75 陈刚(漳州市医院)/76 陈霓(中科体检)。
至此第六批 10 份(67-76)全部验收。

**护栏冻结(三层)**:
- **DB 三件套**: `gen_h004_baseline.REPORT_IDS` 扩至 **48 份**(+72-76); 基线重生成,
  旧条目零变化。`test_h004_baseline_guard.py` 145 passed。
- **结论重提取**: 72/74/75/76 **MATCH**(74 含 visual_sort 链同构); 73 蔡镇宇纯扫描
  **HYBRID 离线锁**(空 `da39a3ee5e6b4b0d`)。
- **指标重提取**: +72 蔡道龙/74 陈东海/75 陈刚/76 陈霓;**73 排除**(纯扫描 VLM 指标,
  规则链无产物); 由 DB 三件套守。
- **全量护栏**: `728 passed / 36 skipped`。

**本轮修复(用户 2026-10-07~08 逐份反馈; 全部有断言/验证, 存量护栏零漂移)**:
1. **蔡镇宇 73(双栏扫描表)**:
   - **不对称双栏按名称列切段**: 表头"项目|结果||项目|结果|参考值|提示|单位"(左半缺列)
     period 判不出时按名称列位置切段独立解析(整行解析左半被右半覆盖丢失);
   - **段首纯单位 token → 整段角色右旋重排**("mmHg|血压（舒张压）|103"/"ng/ml|总前列腺
     特异性抗原" 溢出格错位; 重排后重解, 仍为单位才丢弃) —— 血压（舒张压）103 恢复并
     按提示列标黄、PSA 两项归位;
   - **OCR LaTeX 归一**: `\gamma/\mu/\triangle/\text{}`/`10^{9}` → Unicode
     (Y-谷氨酰转肽酶等可解析);
   - **OCR 内嵌 HTML 表补漏**: 扫描件 OCR 文本的 `<table>` 由 `_parse_markdown_table`
     确定性解析并与 VLM 指标**同名合并**(标志取最大、缺失 ref/单位补齐) —— VLM 漏提
     条目(尿酸/体重指数/收缩压等)恢复标黄;
   - 垃圾清理: "编 码|001259"(`编\s*码` 黑名单)、"血脂四项|3."(分类标题词表补中文数字)。
2. **陈东海 74(双文本层)**:
   - 结论"错位+缺漏"根因: PDF 双文本层 + 线性文本流把标题甩到内容后; 档案加
     `visual_sort` + **双文本层行去重后按 y 重排**(`_dedup_double_layer_lines`) →
     "一、诊断及建议"下各条目标题先于其内容、1-3 条详解补齐(3466 字, 用户 (b)+(c));
   - 指标: 表格占位符 `--` 跳过继续消化参考(尿常规无 ref 105→38 行); 视觉重排后
     表头合并垃圾("单位体检编号"/"中华人民共和国…身高")入黑名单;
   - **结论段来源信号只标黄既有行、不新建行**(`_conclusion_src`): 结论里的碎片
     ("淋巴细胞绝对值|3.2710"等)不再入库, 但陈美杉类"结论行箭头→表格行标黄"保留;
     直接整行剔除信号会让其他区域箭头错配(护栏 21 例假黄), 必须"保留为配对上下文".
3. **陈刚 75**: 结论双语页眉("健康体检报告 MEDICAL EXAMINATION REPORT")与竖线个人
   信息行("陈刚 | 男 | 40 | …")跳过; **乙肝两对半表 dump 序 [名][<参考单限][单位][结果]
   交换**(272.75/0.00/0.37/8.71 正确、参考归位、`<10.00` 假行清零)。
4. **陈霓 76**: "体检号 22783236"(无冒号)跳过; **方括号参考 `[<5.2]`/`[3.5--9.5]` 支持**
   (`_RANGE_RE` 容忍方括号); "数据来源/审核者"黑名单。
5. **翟泽民 69**: "健康知识"断点(科普 Q&A 排除, 1496 字); canonical 别名补全
   ("幽门螺杆菌尿素酶A/B抗体"/"空泡毒/细胞毒抗体" 不再并入"幽门螺杆菌感染"去重);
   分类: 阴离子隙→电解质、4 抗体→胃部幽门螺杆菌检测(读取时计算, 改后须重启**后端**,
   只重启 worker 不生效); "与建议"残留=user-portal `CONCLUSION_TITLE_RE` 补"总检结论与建议"。
6. **弱阳性判黄口径(2026-10-08 用户两轮拍板)**: **结果含"弱阳性" + 与参考范围不一致
   (参考为阴性/缺失无法认定则不算) + 本表无提示/标志列 → 判黄**(VLM/OCR-HTML 表解析
   时置 flag=1)。有提示列的报告(池州粘液丝/防城港中 EB 病毒抗体/钦州二隐血/程永春/
   毕惠丽)保持绿、零漂移; 空泡毒(阴性)绿。
7. **run_rules 暗 bug 根治**: flag=1"无参考范围→黄"分支因 `_to_num(None)` 返回 0.0
   而从未生效 —— 49/59/66 "维生素C ±"生产判绿 vs 护栏镜像判黄口径差的根因; 改用原始
   ref 判空后与镜像口径一致(该类项重跑时转黄, 已知且符合文档口径)。

**已知小残留**: 74 结论中"疾病解释："标签在部分块被 reflow 吞(内容完整);
68 结论条目含"甲状腺实质弥漫性改变"+图注变体(近似重复); 67 条目"右肺上叶后段"长名截断。

### 第七批前 5 份验收与基线冻结(2026-10-13, H004 77-81, 用户逐项验收)

**落库**: 用户单份上传(H004, 归属名"测试6")→ 77 白瑞雪/78 陈炜/79 陈雪婷/80 郭小平/
81 胡月; 逐项修复重跑后验收。**注意: 上传后 worker 是旧进程 → 必须先
`bash /tmp/restart_workers2.sh` 再重跑**(本轮真实踩坑, 一轮白跑)。

**护栏冻结(三层)**:
- **DB 三件套**: `gen_h004_baseline.REPORT_IDS` 扩至 **53 份**(+77-81), 旧条目零变化。
- **结论重提取**: 78 陈炜/80 郭小平 **MATCH**(文本链可复现); 77/81 纯扫描、79 结论文本
  依赖 OCR(hybrid 链) → **HYBRID 锁**(77/81 空 `da39a3ee`, 79 文本层产物 `741c844d`)。
- **指标重提取**: +78/80(80 含红字通道; 护栏链已同步 `red_marks` + **同名兜底上色**)。
- **全量护栏**: `753 passed / 44 skipped`。

**本轮工程(用户逐项反馈驱动, 全部带验证/存量零漂移)**:
1. **恩施州中心医院(80)**: 结论只取"健康指导建议"段(`anchor_only` + 三子部分
   "重要疾病/阳性结果|疾病诊断|阳性发现"齐全); **红字判黄按院启用**(`red_marks`:
   该院仅红字标注异常, 血压/尿潜血/尿白细胞/EB IgG 等 9 项; `_pair_arrow_by_row`
   同排配对 + red 行 hint 清 `CaN` 尾缀 + **同名值形态差兜底上色**) —— 2026-09-08
   全局删红字判黄后按院恢复; EB 病毒抗体 **IgA/IgG/IgM 分型标准**(合并会被
   (standard,result) 去重吞 IgM); "脂蛋白а LP(а)"西里尔字符归一(读取时分类)。
2. **漳州市医院(79)**: 结论标题/化验表为**小截图** → `ocr_content` 小图页条件; 但
   **原文层已是完整单元格转储的页跳过 OCR**(OCR 会丢"单位"列!); **四元组通道**
   (`名称/参考/单位/结果` 四行组, 单位来源) + OCR-HTML 表(**跨页续表表头继承**)
   + VLM 并入(**原文/规则为主**)。
3. **漳州第三医院(78/81)**: "主要问题"锚点、跨页页眉 skip; **tail_anchor/tail_before**
   —— 亚健康处方块(OCR 读取换序到页首)归位到"5、定期复查"之前(报告真实版面: 处方=
   第 5 条正文, 复查是处方第 5 条; OCR 读取顺序错误, 按院档案校正)。
4. **白瑞雪(77)**: 兰州大学第二医院档案 —— 只取"第三部分 体检健康建议"
   (锚点须带冒号, 目录行"第三部分 体检健康建议。"会误命中); 详情段跳过加
   "建议词豁免" + 【N】恢复(建议行"心脏彩超检查提示…建议…"曾被当检查细节起点
   整段跳掉)。
5. **通用**: ◆ 条目标题兜底(恩施"◆幽门螺杆菌感染"逐条, 消 LLM 轮次波动); 终末
   结构去重(短名被含/新见同前变体键); junk 扩展(…结论/样本编号/单位名称/备注/
   结果提示增高/多条目连号长串/"危险因素"科普词/枚举豁免收窄——条件句"若有…"
   不豁免); 名称前缀归一(方法连字符"彩超-"/编号); term_normalizer 去重**字段合并**
   (LLM 行缺单位被规则行补齐) + "参考词当结果"重复行剔除(尿酮|阴性 vs 尿酮|+-)。
6. **前端(user-portal, 不随仓库提交, 需重新构建)**: `CONCLUSION_TITLE_RE` 补
   "健康指导建议"("健康指导"分支吃掉三字只剩"建议"); doctor-portal 无此问题。

**待办**: 第七批其余 6 份(82 黄佳军/83 纪卓武/84 任晓光/85 石硕/86 杨钧予/87 邹佶博)
等用户验收。

### 第七批后 6 份修复(2026-10-14, H004 82-87 用户逐项反馈)

**结论段(档案化)**:
- 82 黄佳军: 恩施档案 extra_anchor 改 [健康指导建议, 本次健康建议](总结段剔除);
- 83 纪卓武: 敦煌康美改只取"【异常结果解读】"(**anchor_only + keep_post_sig** ——
  该段在"分析医师/总检医师"签名块之后, `_first_sig` 会截掉其后锚点; `_strong_sec`
  补"解读");
- 84 任晓光: 详情跳过加**行尾冒号豁免**("心脏彩超:"综述标签不再触发 skip, 彩超内容
  恢复) + **双语标题 CJK 段须 ≥4 字**("三、 PET-CT:" 曾剥成"三、"); 编号内联标题
  拆行保留**源冒号字符**(硬编码全角曾致 21/70/71 基线漂移);
- 85 石硕: 漳州档案 extra_break=[^体检结论汇总](只取分析建议) + extra_skip=签名三行
  (分析建议跨页被签名截断的恢复通道); 信号通道/文本路径历年对比区均覆盖
  "历次检查异常结果比对"变体(上次值 0.22 假黄根因);
- 86 杨钧予: 新档案漳州正兴(anchor_only + ^医学建议); 打印时间戳行入通用 SKIP;
- 87 邹佶博: 新档案红星医院(anchor_only + ^本次体检诊断及建议); 句末黏连
  "【建 议】/【医学解释】"拆行(reflow 前处理, 字符集不变)。

**指标侧**:
- 星号标志: `_norm_flag_cell` 归一 LaTeX "^{*}"→"*", 标志判定三处加 `\*`
  (87 乙肝表面抗体(HBsAb) 阳性 + "*" 标志 → 判黄);
- 跨渠道冲突: **规则行优先**终末过滤(同标准多结果时剔 LLM/VLM 源行: 85 历次对比
  0.22 被 VLM 当本次) + **半定量 vs 纯数字**冲突删数字条(84 维c 5.5 来自单元格
  转储错配, 半定量 +- 为真值);
- LLM 指标解析输入剥离历年对比表(`_shct(text)`, 仅该调用; 结论文本不动);
- 终末名称黑名单过滤(LLM/VLM/OCR 源行不过 table_extractor 的 `_skip_name`);
  `_SKIP_NAMES` +(团体|序号|初步意见), `_SKIP_SUB_RE` +(醫學院|医学院|附屬|附属医院|
  出入境|边防|体检中心$|健康管理中心$, 任晓光封面机构行);
- classify: 血常规 +("分布宽度"), 尿常规 +("维c"/"维C")(82 RDW-CV 归血常规、
  84 维c 归尿常规); 脂蛋白а 西里尔归一(前轮)。
- **护栏**: `753 passed / 44 skipped`(guard17; 结论重提取 49 passed 含本轮全部
  存量零漂移验证)。

### 第七批后 6 份第四轮修复(2026-10-14, 用户逐项反馈)

- **83 纪卓武**: 段尾"敦煌康美综合医院祝您身体健康！"起作断点(其后为分检附录, 用户
  判定多提); 尿蛋白漏提根因 = 全角"＋－"不被值判定识别(`_norm_value_cell`/
  `_is_value_line` 补全角归一) → 尿蛋白|+- 恢复;
- **84 任晓光**: **倒序四元组**([值][↓][参考][单位][名], 该院 OCR 导出序) 通用行式
  把"名"配到下一行值(谷草 27/165 错位) → `_extract_reversed_quartet_rows` +
  **按院开关 `reversed_dump`**(误配模式在其它院校验为高噪声: 冻库 11 处, 默认关);
  结果全角符号归一(存储层);
- **85 石硕**: 历年对比表全链跳过 —— 文本路径剥离(已有)+ **信号通道**+**布局页**
  (`col_rows_with_fallback` 按页标记定位历史页 → `col_rows_via_layout(skip_pages)`)+
  **VLM 图片列表**(ocr_content 分支, 曾误加在 probe 分支)四位一体;
  结论【标题】后接建议/解释拆行(仅非数字【】, 防钦州二【N】枚举被拆断丢"左肾结石");
  "说明："锚行首作 BREAK(防"疾病说明:/检查结果说明:"误截);
- 结果: 85 黄区 = 仅 f/tPSA(用户验收口径), 83 尾干净+尿蛋白恢复, 84 酶类值归位
  (谷草14/谷氨酰27/乳酸脱氢酶165);
- **护栏**: `753 passed / 44 skipped`(全量, 含冻库零漂移)。

### 第七批后 6 份第五轮修复(2026-10-14, H004 83/84 用户逐项反馈)

- **83 纪卓武结论 reflow/locate 四修**: ①`_locate_findings_sections` 正常清单行跳行
  (`_NORMAL_ONLY_RE`) 放宽"（正常值：…）"参考前缀行 —— 尿蛋白解读句
  "(正常值：-）正常人尿蛋白为阴性。尿蛋白出现" 曾被当正常清单行跳过; ②`_split_inline_
  title_body` 不拆"值行（正常值）"标签(血红蛋白 "161/g/L（正常值…）" 曾被拆);
  ③`_reflow_conclusion_lines` 开放尾标题("【血小板数偏高】343"/"【尿蛋白弱阳性】＋－")
  不强行开段, 折行续行拼接 + 值行接单位补空格("343 10^9/L"/"161 g/L");
  ④`_is_plain_finding_boundary` 数值型内容行未收尾(非句号/括号)不拆折行。
  **【N】 后接文字标题("【2】 右肺…结节")仍按标题**(防城港回归); 日期标签行仍走
  pending-date 合并; "2、严格限酒。"/"(参考区间:0.1--0.6)" 等已收尾行仍拆。
- **83 指标侧**: `_SKIP_SUB_RE` 补 `正常参考值|小结$|报告单|条码号|标本号`;
  `_SKIP_NAMES` 补 `龄`(年龄残片); `_merge_wrapped_names` 守卫(值短语结尾行
  "形态正常/不肿大/无畸形" 不作折行首段、单汉字行"齐"不并名) —— 敦煌检体区
  "正常参考值身高/形态正常四肢/齐心界/不肿大甲状腺"等合并垃圾全清;
  **"弱阳性"注释行 flag**: 值后独立"弱阳性"行且结果为±形态 → flag=3
  (尿蛋白|+-|黄 —— 用户口径"标志列弱阳性应提取"; 程永春/毕惠丽结果列弱阳性零漂移)。
- **84 任晓光**: `_extract_reversed_quartet_rows` 重写为**变长行组**(名前行 1..4 cell,
  无单位/无范围行也解析) —— 旧固定四元组漏掉"尿比重/1.030/1.003-1.030"等行给通用
  行式 → "名配下一行值"错位(尿比重|+-); 单位样名("Cell/uL")不作名称候选;
  倒序医院信号通道守卫(同名已有不同数值结果时丢定性信号)。
  终态: 尿比重|1.030 绿、维 c|+- 黄、尿潜血/隐血恢复、酶类值归位。
- **护栏**: 全量 `753 passed / 44 skipped`; 结论重提取 49 passed/22 skipped
  (两处 hash 漂移已修: A1 放宽仅限"（正常值"前缀行, 一般叙述行仍跳)。

### 第七批 11 份全量验收冻结(2026-10-14, 用户"后6份核验完成，加入基线护栏")

**护栏冻结(三层, 82-87 并入)**:
- **DB 三件套**: `gen_h004_baseline.REPORT_IDS` 扩至 **59 份**(+82-87); 基线重生成,
  旧条目零漂移。
- **结论重提取**: 82 黄佳军/83 纪卓武/86 杨钧予 **MATCH**(文本链可复现);
  84 任晓光(文本层为单元格转储, 离线锚点定位不到)/85 石硕(结论链依赖 OCR 补文)/
  87 邹佶博(纯扫描) **HYBRID 离线锁**(空 `da39a3ee5e6b4b0d`; 生产文本由三件套守)。
- **指标重提取**: +82/83/84/85/86(84 的**信号通道 reversed_dump 守卫已同步进护栏链**);
  **87 排除**(纯扫描 VLM 指标, 规则链无产物; 由 DB 三件套守)。
- **全量护栏**: 见下(最终一轮)。第七批 11 份 = 77 白瑞雪/78 陈炜/79 陈雪婷/80 郭小平/
  81 胡月/82 黄佳军/83 纪卓武/84 任晓光/85 石硕/86 杨钧予/87 邹佶博 全部验收并冻结。

## OCR 服务稳定性加固(2026-10-14, 看门狗+串行化+重试)

**背景**: PaddleOCR-VL(8001) 长跑后偶发 `RuntimeError: int(Tensor) is not supported
in static graph mode`, 进程内无法自愈(必须重启); 此前无自动恢复、`/health` 是浅检查
(不碰模型)、启动日志覆盖写丢弃崩溃 traceback、解析侧对 OCR 页失败**静默缺页**。

**四层加固(全部已实机演练/单测)**:
1. **服务端串行化 + 统计**(`paddle_ocr_service/main.py`): `threading.Lock` 串行化
   `pipeline.predict`(Paddle 管线非线程安全, 多 worker 并发是长跑崩溃的可疑诱因之一;
   实测 3 并发请求严格串行完成); 新增 `GET /stats`(pages/failed/inflight/uptime/
   model_loaded); predict 异常显式 `logger.exception` 保证 traceback 落盘。
2. **OCR 看门狗**(`app/core/ocr_watchdog.py`, 随 FastAPI 8000 startup 启动, 同
   interp_watchdog 模式; 配置 `OCR_WATCHDOG_*`):
   - **深度探活**: 内嵌 200x60 小图真实调 `/ocr`(非 /health), 默认每 120s;
   - **故障重启**: 连续 2 次失败 → 按**端口**(fuser -k)杀本机 OCR 进程并同 start.sh
     命令重启(绝对路径 paddle_venv), 等待就绪; 重启防抖 90s;
   - **滚动重启兜底**: 累计处理 ≥2000 页或运行 ≥4h 且 inflight==0 时主动重启;
   - 全量日志 `app.ocr.watchdog` → app.log。
   **实机演练**(2026-10-15): kill OCR → 探活 streak 1/2 → streak 2/2 → 自动重启 →
   21s 后恢复, 全程 ~2.5 分钟无需人工。
3. **客户端重试**(`vlm_client.VLMClient.extract_from_image`): 连接错误/超时/5xx
   重试 `OCR_CLIENT_RETRIES`(默认 2, 退避 1s/3s); 4xx 不重试。
4. **故障上抛落 stage**: 大面积失败(全部失败或 ≥max(2,50%)页) → 抛
   `OcrUnavailableError`(`_ocr_outage` 判定; 个别页偶发失败仍按历史行为跳过) →
   parsing worker 记 `failed_stage=ocr_failed`(**可重试**, 不在 UNRETRYABLE 集合),
   不再静默产出缺页报告。覆盖 `_extract_pdf_text(hybrid)` 页循环与
   `extract_from_images`(两处 `except` 已改为穿透 OcrUnavailableError)。
5. **start.sh**: OCR 日志改 `>>` **追加**保留(崩溃 traceback 不丢)。

**测试**: `tests/test_ocr_watchdog.py`(探活/重启/防抖/滚动/恢复 10 条)、
`tests/core/test_vlm_client_ocr.py`(重试/4xx不重试/outage 阈值 15 条)、
`tests/test_main_wiring.py::test_ocr_watchdog_starts_on_startup_and_cancels_on_shutdown`。
**全量测试状态(2026-10-15 清理后)**: `pytest tests/` = **1331 passed / 47 skipped /
0 failed**。此前 13 个历史遗留失败已全部清理(纯测试侧, 零业务代码改动):
11 个源自 2026-09-13 的 `50ad9b9` 分支合并(app 侧测试配 merge 侧代码)——
parse_category×3/report_parsed_name×5(补版面接线引入的坐标列/信号 mock;
category 组显式走 `INDICATOR_EXTRACTOR=llm` 分支验证 category 落库契约)、
user_profile×2(标准名对齐院方标准表: 血小板数(PLT)/血小板压积(PCT))、
interp_worker_risk(退役钩子 `try_generate_comparison_summary`→`ensure_change_overview`);
1 个 `f967744`(2026-08-06 统计模块高风险口径 `IN ('red','yellow')`);
1 个为 checkout 迁移丢文件(《体检项目指标标准表.md》已从旧 checkout 拷回, 该文件在
.gitignore 属有意不入库); 另有 2 个测试时间炸弹已修(logging rollover mtime /
stale classify 断言)。

## 影像检查 11 项红黄绿总览(2026-10-14, 报告详情)

**事实**: 医生端与 user-portal 报告详情页新增"影像类结果分级"卡片(两列色块, 红→黄→绿
排序, 同级保持接口顺序) —— 11 项检查
(心电图/胸部X片/胸部CT/彩超心脏/甲状腺/颈动脉/腹部+男泌尿/子宫附件/乳腺/腹部+女泌尿/
头颅CT核磁)各自红黄绿; 男 8 项(无子宫附件/乳腺/腹部+女泌尿)、女 10 项、gender NULL 11 项。

- **数据源 = 双源并集(零重跑/零管线改动/不碰基线)**: 源A = 最新 completed 解读的
  `indicator_judgment(source='conclusion')` 条目; 源B = `report_info.conclusion_text`
  原文收紧扫描(表头/正常句/化验行/长句截断/条件句守卫 过滤); 每项取最高级。
- **代码**: 纯函数 `backend/app/modules/report/imaging_items.py::build_imaging_items(text,
  gender, entries)`; `service.get_conclusion_entries(db, report_id)`; schema
  `ImagingItemSchema`; `GET /api/v1/reports/{id}` **纯追加**字段
  `imaging_items: [{name, level}]`(level ∈ red/yellow/green; 空文本且无条目 → `[]`,
  前端显示"未提取到结论")。前端: 两端 `ReportDetailPage.tsx` 结论卡之后的色块卡片。
- **口径要点(改词表前先看 spec)**: 化验类词整体遮蔽(LAB_MASK); 裸"肺"带负向断言
  (`肺(?!动脉|功能|通气|活量|弥散|静脉)`, 防"肺动脉瓣"又保"肺增殖灶"); 不用"脑血管"
  (科普泛称, 心脑血管疾病≠脑部影像); **"椎动脉"不算彩超(颈动脉)**(独立椎动脉彩超,
  用户 2026-10-14 审表口径); "左心室高电压"归心电图不算心脏彩超; "颈部淋巴结"不命中
  颈动脉; 红=保守(行动句+强紧急词+条件句守卫, 当前语料预期近零)。
- **验收工具**: `backend/scripts/check_imaging_items.py`(77 份离线审表+命中证据, 只读);
  纯函数断言 `backend/tests/modules/report/test_imaging_items.py`(11 条, 秒级, 含用户
  两轮审表反馈回归)。审表方法已沉淀: 先跑审表给用户按**姓名**审(用户会逐份核对), 收敛后
  再动前端。
- **spec**: `docs/superpowers/specs/2026-10-14-imaging-items-module-design.md`
  (含替代路线否决记录/过滤细则终稿/实现记录)。

### 崇左市人民医院(滕寿权)结论段与换行修复(2026-10-15, 用户反馈)

- **结论只取"医生建议:"段**: `report_profiles.py` 新增崇左档案(keywords=["崇左市人民医院"],
  anchor_only=True, extra_anchor=[r"^医生建议"]) —— 报告前半为分科检查结果汇总
  (【血脂四项】/【肝功能】/【心电图】/【B超】…), 通用锚点曾把汇总段一并收进结论。
  **指标侧不受影响**: process_task 里挖结论段(finding_sec)用的 `_locate_findings_sections`
  **不传 anchor_only**(只传 extra_*), anchor_only 仅作用于结论提取
  (`_extract_conclusion_async`)。
- **reflow 伪编号折行**: "数字**直接接**文字/字母/×"的行("2mm（左）"/"024一06一18"/
  "2类）")曾被 item_head 当新编号条目断开 → 新守卫: 上行未以句末/括号收尾时拼接
  (真编号条目数字后有分隔符"1、"/"2. "不受影响)。验证: "其中一大小约2×2mm（左）、
  3×2mm（右）"/"您2024一06一18的体检数据为：" /"高密度脂蛋白胆固醇降低1.10mmol/L"
  均成整句; "甲状腺结节TI一RADS 2/3类…"正常。
- **验收**: H003-1 与 hospital_1-38(同报告另一租户副本)均已 reprocess; 结论 1976 字
  (仅医生建议), 指标黄红 8 项**零变化**; H003 基线重生成(仅报告1条目更新, 其余零漂移);
  全量 `pytest tests/` = 1342 passed / 47 skipped / 0 failed。
- 样本断言同步: `conclusion_samples.py` chongzuo_tengshouquan(must_contain 改建议段内容,
  expected_titles 去掉汇总段标题)。

### 第八批 11 份适配与端到端(2026-10-16, H004 88-98)

**落库**: 88 包强/89 陈宏宏/90 陈佳漩/91 陈坤铭/92 陈婷婷/93 陈行富/94 陈阳/
95 封猛/96 付本明/97 韩先锋/98 李承龙。

**新医院档案**(report_profiles.py, 全部 anchor_only):
- 临沂市中医医院(98): 锚点 `^医学建议`(无冒号, 通用锚点不命中; 与日照中医同模板)。
- 昆明医科大学第一附属医院(92): `^体检阳性结果分析及建议`; extra_skip 总检医生/日期
  (签名插在第 13/14 条之间) + `^主检医师温馨提示`(正文标题非签名)。
- 温州医科大学附属第二医院(88): `^主要健康问题与建议`; extra_break `^国际体力活动问卷`。
- 复旦大学附属中山医院(90, 厦门): `^结论汇总` + extra_break `^医学名词解析`(汇总+分析,
  科普不入)。
- 联勤保障部队第910医院(91): `^医生建议[:：]`; extra_skip 审核时间/装订(段尾字段)。
- 开滦总医院(97): `^阳性结果汇总$`(目录行"2. 阳性结果汇总"不命中); extra_break 合理建议/补充说明。
- 厦门大学附属中山医院(89): `^体检结论分析`; **文本层字体映射乱码, 生产须
  `FORCE_OCR_TASKS`(task 89)强制逐页 OCR**(`_force_ocr_task_ids` 现读 .env 的
  `FORCE_OCR_TASKS` settings —— 新增 Settings 字段 + 环境变量回落)。
- 厦门大学附属心血管病医院(93): `^体检结论分析` + extra_skip 裸日期页脚(`^\d{4}-\d{1,2}-\d{1,2}$`
  按院声明 —— 全局裸日期跳过会误伤存量(79 锁定漂移, 试过已回退))。
- 96 付本明(日照市中医医院) = **无结论段样本**: 定位返回 None; LLM 兜底新增
  "纯签名/标签壳→判无结论"守卫(`_LABEL_ONLY_RE`); e2e wipe 现清 `conclusion_text`
  (否则 reprocess 残留旧值)。最终 conclusion_text = NULL(专项测试
  `test_no_conclusion_sample_returns_none`)。

**全局 locate/reflow 修复(带护栏验证, 存量零漂移)**:
- `_EXAM_DETAIL_START_RE`: 上皮细胞/管型等词**后接冒号**("上皮细胞: 阳性")是行内检验结果,
  不触发 skip_detail(中山厦门"结论汇总-六、检验"曾被整段跳过)。
- 裸 2-4 字行后跟签名(总审/总检/主检…医生)或裸名簇再签名 = 医生姓名, 跳过
  (中山厦门结论尾"汤卡卡/陈宁"); **上一行是"标签："时不跳**(报告整理：郑秀凤 —— 名字是标签值)。

**OCR 稳定性(本轮实测补充)**:
- 实测 int(Tensor) 崩溃间隔约 **15-70 页**(上游 paddlex vlm worker); 滚动重启阈值
  `OCR_WATCHDOG_ROLL_PAGES` 2000→**50**(空闲窗口主动换桶)。**进程内重建 pipeline 无效**
  (试过, 状态不复原), 进程级重启是可靠路径, 已回退该改动。
- **OCR 页级缓存(内容寻址)**: `OCR_CACHE_DIR`(默认关; sha1(图片)→markdown)。长报告
  崩溃重试只补未缓存页 → 可收敛(93 陈行富 19 页 OCR 第 2 次重试成功即靠此)。
- e2e 工具: OCR 故障自动等待探活恢复后 wipe 重跑(最多 2 次额外); 单报告失败记录后
  **继续后续报告**(不再整批中断)。

**端到端结果**: 11 份全部 completed(92/93 曾因 OCR 崩溃失败, 重跑成功); 结论 10 份
正常(88 1489/89 697/90 613/91 1047/92 1411/93 929/94 459/95 1379/97 877/98 965 字),
96 = NULL(无结论段); 各报告黄红判定均生成, 待用户前端验收。
护栏: `tests/modules/report/ + tests/test_safety_net.py` = 799 passed / 49 skipped。

### 第八批前 5 份验收冻结(2026-10-19, H004 88-92, 用户"加入基线护栏")

**护栏冻结(三层)**:
- **DB 三件套**: `gen_h004_baseline.REPORT_IDS` 扩至 **64 份**(+88-92); 基线重生成,
  旧条目零漂移。
- **结论重提取**: 88 包强/90 陈佳漩/91 陈坤铭/92 陈婷婷 **MATCH**;
  89 陈宏宏(文本层字体映射乱码, 生产 FORCE_OCR_TASKS=89) **HYBRID 离线锁**
  (`da39a3ee5e6b4b0d`; 生产文本由三件套守)。
- **指标重提取**: +88/90/91/92(文本规则链可复现); 89 排除(force-ocr, 规则链无产物)。
- **全量护栏**: tests/modules/report + test_safety_net = 873 passed / 49 skipped
  (含读取层分类新单测)。

**本轮修复(用户逐份反馈, 全部纯读取层/规则层, 详单)**:
- **89 陈宏宏**: LaTeX 行内包裹结果归一("阴性[ $ <0.10 $]" → "阴性[<0.10]");
  参考说明/纯区间括注误入结果剥离("7.16（合适水平：…）"/"1.63（0.55～1.30）" → 纯值,
  与正确行归一去重)。
- **90 陈佳漩**: 结论只取"结论分析"(profile 改锚点 + `no_colon_strip=True` —— 该段是
  "条目名:建议"标签式, 冒号剥离会把条目名删光致 LLM 提取 0 条); 风险"冒号结尾条目行+
  上段已收尾→换行"; 心脑血管病风险评估页整页过滤(布局 skip_pages + 文本页过滤);
  机构抬头前缀式识别(允许数字, 联勤第910医院/复旦厦门重复名); 患者"姓名+性别+年龄"
  合并行清理; junk-context 对"标签式条目"豁免(`^名[:：]` / `；名[:：]`);
  影像匹配屏蔽科室名(肾病科门诊建议曾被"肾"命中腹部+泌尿彩超 → 假黄)。
- **91 陈坤铭**: 结论页眉(日期/第N页/预约号/医院名)按院 extra_skip; layout `prev`
  角色补"上次/上上次"(6 列表历史列不入库); 信号通道"参考值(阴性)+↑"误配守卫。
- **92 陈婷婷**: 分类补充 —— 尿素酶/细胞毒素/空泡毒素/胃蛋白酶原/PGI/PGII →
  胃部幽门螺杆菌检测; 锌 → 电解质; PH值(PH) → 尿常规; CKMB/CK → 心肌酶谱;
  双栏"标签:日期+下一标签"名称拆分(末次月经/孕产史)。
- **血/尿同名系统修复(读取层, 用户点名)**: `_BLOOD_UNIT_RE` 原只认 10^12 不认
  **10^9/L**(白细胞/血小板), `_URINE_UNIT_RE` 含裸 /l 把**任何 /L** 当尿单位 → 血常规项
  整批误分尿常规。已修(血单位 10^9/10^12; 尿单位仅 /μL、/uL、/ul、/HP、/LP…);
  血常规全名带尿单位 → 尿常规(仅单位, 血单位恒血常规); **白带上下文**: 报告含清洁度/
  滴虫/霉菌/加特纳/线索细胞时, 共享裸名+体液单位+无"镜检："前缀 → 白带常规;
  加特纳/线索细胞归白带常规。`test_indicator_groups.py` +3 单测。

### 第八批后 6 份验收与基线冻结(2026-10-20, H004 93-98, 用户逐项验收"通过")

**落库/验收**: 93 陈行富(厦门大学附属心血管病医院)/94 陈阳(中国中医科学院望京医院)/
95 封猛、96 付本明(日照市中医医院)/97 韩先锋(开滦总医院)/98 李承龙(临沂市中医医院)。

**护栏冻结(三层)**:
- **DB 三件套**: `gen_h004_baseline.REPORT_IDS` 扩至 **70 份**(+93-98); 基线重生成,
  旧条目零漂移(仅 +857 行新条目)。
- **结论重提取**: 94/95/97/98 **MATCH**(97 需 reguard 链同步 `keep_bare_numbers`);
  93 陈行富(扫描混合件, 生产 `ocr_content_pages` OCR) **HYBRID 离线锁**;
  96 付本明(无结论段样本, 生产 NULL) **HYBRID 空产物锁**。
  另 reguard `_re_extract_text` 补传 `keep_bare_numbers`(与生产 `_extract_conclusion_async`
  同构)。
- **指标重提取**: +94/95/96/97/98(文本规则链可复现; 94 red_marks_ref、95/96
  summary_marks、97 latin_wrap 均已同步进护栏链); **93 排除**(扫描混合件, 规则链无产物),
  由 DB 三件套与结论护栏守。
- **全量护栏**: `tests/modules/report/ tests/test_safety_net.py tests/core/` =
  **937 passed / 52 skipped**(2026-10-20 收口)。

**本轮修复(用户逐份反馈; 全部带断言/离线+端到端验证)**:
1. **94 陈阳(望京)**: 新增 `red_marks_ref` 红字通道 —— 红字=报告方异常标注, **有参考范围
   即判黄**(PH 5 打印参考 4.6-8.0 仍标红 → 黄; 天冬氨酸氨基转移酶 13 同判); 无参考的
   红字(身高/体重/腰围/臀围)保持绿。另: 双列名称按阅读序(y,x)拼接 + 竖直居中序号剔除;
   单位/参考折行续接。
2. **95 封猛(日照市中医)**: 新增 `summary_marks` —— 总结段【异常项】给**既有**指标行
   标黄(不新建行; 每个条目只标最长匹配行): 红细胞(隐血)1+/蛋白+- 判黄; 尿胆原结果"-"
   时其后的"弱阳性"是**参考范围列**不是异常标志(同误黄也清除 96 付本明)。
3. **93 陈行富(厦心血管)**: 首页姓名栏**坐标配对**(标签/值分列时文本流取不到姓名值,
   曾退回"尊敬的") → 姓名/parsed_name=陈行富; `ocr_content_pages=True` 补 OCR 跨页结论
   续段(前列腺增生伴钙化/尿酮体阳性/尿比重增高/肌酸激酶降低/血常规异常/乙肝两对半);
   "清亮/清澈/淡黄色/黄色/浑浊"入定性值词表(不再并名成"清亮尿PH")。
4. **97 韩先锋(开滦总医院)**:
   - 结论: 编号条目**序号+异常名独立成行**、科普/建议正文另起一行; 正文折行拼接
     ("也可见于血液系统疾病"/"慢性间质性肾炎"/"提示血脂代谢异常"/"桥本甲状腺炎");
     编号正文内不做"短标题→长解释"拆行(`in_num_body`)。
   - 指标: `keep_bare_numbers`(条目序号保留, `match_profile` 白名单登记);
     `latin_wrap`(中文名+纯拉丁缩写续行: MON%/LYM%/GRA%/PSA/FPSA; 仅开滦声明,
     防已验收院改名漂移); "检测值|结果"**双值表列语义**(检测值→结果, 结果列→flag2
     异常标注列): 潜血 2+/50 → 黄, 电导率信息3级/尿素14C呼气试验/14碳呼气试验/
     红细胞信息/谷丙转氨酶* 正确入库; 行式"半定量结果+纯整数计数"判黄; 范围折行拼接
     (0.108-0.282 / 1.003-1.030 / 5000-12000); 单位折行取完整(mmol/L); 定性值带拉丁
     标注("阴性Hp(-)")识别为结果(不再重复行); 垃圾名(合理建议/参检单位/受检单位/
     室间隔厚度/左室后壁厚度/名称内测量值)过滤; "70%红细胞散射光强度"名称前缀允许 %。
5. **98 李承龙(临沂市中医)**: "历次部分结果对比"表**跨页**全链屏蔽(信号通道按
   标题页+后续纯日期页整页跳过; 布局按纯日期单元格 region 跳过) → 总胆固醇5.10 不再
   假黄, 碳呼气试验/幽门螺旋杆菌抗(历史表"胃幽门螺旋杆菌-阳性↑")/舒张压59/脉搏/
   体重指数25.1/未查/PDW历史值全部清除; 真 PDW 16.6(黄)保留。
6. **读取层分类(后端重启生效)**: 裸 `PH`/`尿PH` → 尿常规; 同名"葡萄糖"按结果区分 ——
   非数值(尿糖"-"/"阴性")→尿常规, 数值(mmol/L)→空腹血糖。
7. **跨通道**: 行式 `result+unit` == 列式 `result`("电导率信息" 行式"3"+"级" vs
   列式"3 级")→ 保留列式不重复建行; 列式 ref 截断(高位≤低位或行式高位更完整)时取
   行式完整 ref; 列式单位是行式单位前缀("mmol/" vs "mmol/L")时取完整单位。

### 第九批前 5 份验收与基线冻结(2026-10-22, H004 99-103, 用户逐项验收完成)

**样本**: `体检报告样例/1-第9批/`。id: 99 常海毅(牡丹江医科大学附属红旗医院)/
100 高亮(爱康卓悦贵阳)/101 龚文怡(同济医院)/102 胡艳(武汉市中心医院后湖院区)/
103 姜兆龙(三亚边检模板, PDF 无医院名)。

**护栏冻结(三层)**:
- **DB 三件套**: `gen_h004_baseline.REPORT_IDS` 扩至 **75 份**(+99-103); 基线重生成,
  旧条目零漂移(仅新增 566 行)。同时重生成/校验 **H003 18 份基线**
  (`gen_h003_baseline.py` → `h003_baseline.json` 零 diff; `test_h003_baseline_guard.py`
  全绿), 确认本轮规则改动未影响既有 H003 验收态。
- **结论重提取**: 99/100/101/102/103 全部 **MATCH**。reguard 链同步了新档案后处理
  (`nfkc`/`anchor_only`/`numbered_advice`/`collapse_repeats`/`attach_leading_numbers`/
  `clean_important_head`); 99 额外传 `skip_lines={"常海毅"}`(生产 parsed_name 页眉)。
- **指标重提取**: +99/100/101/102/103(`_BASELINE_CASES`); 护栏链同步
  `layout_only`/`infer_columns`/`summary_marks_from_text`/`bmi_marks`/`lab_summary_marks`。
- **样本库**: `conclusion_samples.py` 已登记 B9 10 份(宋文军纯扫描 `ocr=True`)。
- **全量护栏**: `tests/modules/report/ tests/test_safety_net.py tests/core/` =
  **986 passed / 53 skipped**(2026-10-22 收口)。

**本轮修复(用户逐份反馈; 全部带纯函数断言/离线+端到端验证)**:
1. **99 常海毅**: 档案 `visual_sort` + `anchor_only` `^◆主要健康问题`, 保留摘要/次要
   问题/一、二条目正文; `infer_columns`+`layout_only` 解析无表头化验表, 段级形态门
   改为"丢坏行保好行"(25-羟基维生素D/铁蛋白恢复); `lab_summary_marks` 按
   "1.小圆细胞偏高：3.1"类小结标黄; 小圆细胞/尿钙归尿常规; 胸部CT 假黄来自肌酸激酶
   科普枚举行(已过滤)。
2. **100 高亮**: 部首补充区归一(⻔→门/⻩→黄/⻅→见; `_CJK_RADICAL_MAP`); 结论
   **只取 `2.专家建议与指导`**(用户口径: 前半"体检重要异常结果"的 01 便隐血/结果说明
   不提取); `numbered_advice` 把 01–12 序号归位并拆粘连标题; `summary_marks_from_text`
   + `bmi_marks` 保留前后半摘要标黄(体重指数/腰围/便隐血等); 便颜色/性状/红细胞/
   白细胞/便常规其它归大便常规; `V1V2` 导联垃圾过滤。
3. **101 龚文怡**: `collapse_repeats` 折叠同层叠字; `attach_leading_numbers` 恢复
   疾病诊断/阳性发现序号并合并编号标题折行; 长裸数字页眉跳过。
4. **102 胡艳**: 结论 `anchor_only` 只取 `^异常解读`; 影像子宫附件假黄来源为妇科查体
   字段(`宫颈:…`/`子宫位置:`)与"宫颈防癌液基…" → 已过滤; 胸部CT 黄为肺结节真实所见。
5. **103 姜兆龙**: 合并表头"单位参考范围"按末两列补 unit/ref; 参考折行整组拼接
   (1.05--1.75/0.75--1.02/0.85--1.51/3.89--6.11, 中文"小于/大于"归一);
   尿钙归尿常规; 腹部+男泌尿彩超假黄来自"肾脏受损、尿毒症期…"科普句 → 已过滤。
6. **红区语义(2026-10-22 用户拍板)**: `及时就诊/及时就医/及时复诊/尽早/重视` 均为
   红区触发词; 影像红**级联结论条目红**, `origin_line` 指向命中建议句(前端红色加粗);
   LLM 单独 `is_urgent` 无确定性触发词时降级; 完全相同建议被复制给多条时只保留建议行
   前最近 8 行内出现名称的条目(高亮 Wellen 保留/窦缓清除)。
7. **读取层**: `normalize_compat_chars` 扩到 CJK 部首补充区; `classify` 先归一再分栏
   (天⻔冬氨酸氨基转移酶→肝功能); 影像过滤妇科查体字段/化验解释枚举行/肾内科解释句。

### 第九批后 5 份验收与基线冻结(2026-10-22, H004 104-108, 用户逐项验收完成)

**样本**: `体检报告样例/1-第9批/`。id: 104 刘振宇(葫芦岛市第二人民医院)/
105 宋文军(临沧市人民医院, 纯扫描)/106 宋玉枣(保山市第二人民医院)/
107 甄元(山东阳光融和医院)/108 周业潮(江门五邑中医院)。

**护栏冻结(三层)**:
- **DB 三件套**: `gen_h004_baseline.REPORT_IDS` 扩至 **80 份**(+104-108); 基线重生成,
  旧条目零漂移。H003 18 份基线同步重生成校验零 diff。
- **结论重提取**: 104/106/107/108 **MATCH**; 105 宋文军纯扫描(生产 hybrid 探针提文)
  **HYBRID 空产物锁**(`da39a3ee5e6b4b0d`)。
- **指标重提取**: +104/106/107/108(`_BASELINE_CASES`); **105 排除**(纯扫描 VLM 指标,
  规则链无产物), 由 DB 三件套与结论护栏守。
- **样本库**: B9 10 份已含 104-108, 无需新增。
- **全量护栏**: `tests/modules/report/ tests/test_safety_net.py tests/core/` =
  **1012 passed / 54 skipped**(2026-10-22 收口)。

**本轮修复(用户逐份反馈; 全部带断言/离线+端到端验证)**:
1. **104 刘振宇**: 结论只取 `本次健康建议`; 前端标题按 `*` 加粗、`(1)` 建议正文不加粗;
   小红细胞/大红细胞/抗坏血酸归尿常规; 铁(Fe)归电解质; 尿糖非数值行归尿常规并显示
   `尿葡萄糖（GLU）`(与血糖 `葡萄糖(GLU)` 区分)。
2. **105 宋文军**: `clear_flags` 清除 VLM 误标的 RDW-SD/MPV/MCV; 血细胞比容归血常规。
   **单位与参考值合并列未改**(纯扫描 OCR/VLM 把 `4.30-5.80 10^12/L` 压成一列,
   可靠拆分需表级列切分/版面识别, 风险高; 用户同意先保留)。
3. **106 宋玉枣**: 过滤 `操作：…/审核：…`、`良好目前不适感觉`、`心电图 1`/
   `心脏彩超 1`、`双侧锁骨下动脉 1`、`泌尿系超声-男 1`; 腺苷脱氨酶归肝功能;
   隐血试验/粪便红细胞/粪便白细胞归大便常规; 尿精子定量/尿杆菌定量归尿常规。
4. **107 甄元**: `strip_unit_flags` 剥离单位/参考中的 `↑↓` 并置 signal_flag=2
   (标志列无表头、与单位同格); 白细胞 18.00/红细胞 0.00/细菌/结晶归尿常规;
   乳酸杆菌比例/其他杂菌/白细胞(10-15 /HP)归白带常规; 乳腺彩超按"左乳结节"标黄;
   `您于2026-07-04` 垃圾行清除。
5. **108 周业潮**: 结论只取 `异常解读`; `粘液` 归尿常规; `周业潮男·` 垃圾行清除;
   MCV/GRA%/GRA 归血常规, 5’-核苷酸酶(5-NT) 归肝功能。
6. **读取层**: 葡萄糖同名非数值→尿常规并加"尿"前缀；白带同名覆盖仅限 `/HP|/LP`
   单位(/μL 是尿常规计数, 不再误归白带); 考试类垃圾行(超声/动脉 + 裸序号)过滤。
7. **前端**: `renderConclusionText` 对 `*` 开头行加粗; `(1)/(2)` 建议子项不再按编号加粗
   (doctor-portal/user-portal 各一处; 非后端文件按约定不提交)。
