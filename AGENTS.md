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

## OCR 服务 8006 与 workers 环境(2026-09-03 起)

**事实**:
- `backend/.venv`(主 venv)不含 paddle;OCR 用 8006 实例 —— 由 **wjyy2 的 uv python +
  PYTHONPATH 指向共享 site-packages** 启动(paddle 包本体在 root 的
  `/data/project/hospitalKnowledgeBase/backend/paddle_venv/lib/python3.10/site-packages`,
  wjyy2 无权限直接执行该 venv 的 python,只能借 site-packages)。
- 启动命令(8006 若丢失):
  ```bash
  cd backend
  SP=/data/project/hospitalKnowledgeBase/backend/paddle_venv/lib/python3.10/site-packages
  PYTHONPATH=$SP setsid nohup \
    /home/wjyy2/.local/share/uv/python/cpython-3.10.20-linux-x86_64-gnu/bin/python3.10 \
    -m uvicorn paddle_ocr_service.main:app --host 0.0.0.0 --port 8006 \
    >> /home/wjyy2/logs/paddle-8006.log 2>&1 < /dev/null &
  ```
- `scripts/start_workers.sh` 已 `export OCR_BASE_URL=http://localhost:8006`(workers 的
  图片型报告走 8006;系统级 8001(paddle_venv)对扫描页会 500,不要切回)。
- 重启 workers 只 kill `/home/wjyy2` 侧(`grep -v /data/project`),root 侧(/data/project
  下多组 worker + 8001)不能动。

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
且含≥2图"的页自动调 PaddleOCR(8006)补文本 —— 福建第二(文本化验页+图片结论页)
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
- **8006 OCR 500 处理**: 重启 8006(命令见"OCR 服务 8006"节)后 OCR 恢复, 图片型
  报告(福建第二)结论页可正常提取; 批量重跑前先 curl 测一页 OCR。
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
   需要看页面结构时 `LAYOUT_DEBUG=1`; 图片型结论页 `--hybrid` + `OCR_BASE_URL=http://localhost:8006`。
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
- 福建第二(OCR 图片结论页): 结论页 2/3/5 号偶漏属 OCR 不稳定, 8006 重 OCR 可补全
  (响应字段是 `markdown` 不是 `text`); 补全后重建 `conclusion_text` 再提取。

### 环境/操作备忘(2026-09-05/06 新增)
- 23:56 批 13 份 PDF 在 `backend/storage/H001/batch/extracted/b4825d.../`; 广西/北京样本在
  仓库根 `体检报告样例/`(指标护栏直接引用)。两组 H003/H004 task/report id 20-27。
- 该批报告结论区/指标区数据多轮手工重跑落库; 再改提取规则后如需同步线上, 参照
  "删表格式行→pipeline 落库→删 interpretation 重投→验证"流程(worker 会用 backfill 自动补结论)。
- 8006 PaddleOCR HTTP 接口: POST /ocr, body `{"image_base64": ...}`(multipart 会 500),
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
  --report-id N --sync --db XXX [--hybrid]`(OCR 结论页报告需 --hybrid + OCR_BASE_URL=8006)。

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
- 端到端(8 家金标 process+解读, OCR 8006): 黄红名单干净且与金标一致 ——
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
- 事故与修复: 钦州(欧阳庆)PDF 为纯扫描件(每页 1 图), 重跑时 8006 OCR 服务异常
  (int(Tensor) 报错)导致指标被清成 0; 重启 8006 后重跑恢复 119 行(与基线一致)。
  教训: 扫描件重跑前先确认 8006 健康; process 需在 backend cwd 运行(否则 .env 未读,
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
