# Agent 接力工作文档:批量上传按「姓名 + 身份证后六位」双锚定分发

**交接日期**:2026-09-01
**交接分支**:`feat/local-model-import`(当前)
**交接基点**:HEAD = `1784498`(spec 提交)
**交接状态**:实现已完成并通过 15 个任务的逐任务审查 + 最终整支审查;工作区有 39 个已修改文件 + 6 个未跟踪新文件,**尚未 commit,尚未迁移,尚未配置外部接口**。

> 本文件是给下一个接手的 Agent 的工作接力入口。先读本文件,再读
> `docs/superpowers/plans/2026-09-01-batch-upload-idcard-suffix.md`(含 §15 增量)
> 与 `docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md`(含 §15)。
> SDD 过程记录在 `.superpowers/sdd/progress.md`(逐任务审查结论 + 遗留项)。

---

## 1. 功能一句话

批量上传 zip 的文件命名从旧 `<姓名>_<医院编号>_<用户编号>.<ext>` 改为 `<姓名>_<身份证后六位>.<ext>`,
通过可配置外部接口(`EXTERNAL_RESOLVER_URL`)把后六位解析成用户所在医院的 `hospital_id` 并跨租户落库;
用户身份锚定从平台数字 user_id 改为 **姓名 + 身份证后六位** 双条件,全链路(报告列表 / user_profile / chat / AI agent 工具 / interp trend)按 `user_id == 后六位 AND name == 姓名` 过滤。

---

## 2. 已完成(已实现 + 已审查通过,未 commit)

| 模块 | 内容 | 关键文件 |
|------|------|---------|
| resolver | 外部接口客户端:配置 `EXTERNAL_RESOLVER_URL`/`TIMEOUT`,返回 `hospital_id`/`None`/抛 `ResolverUnavailableError`;4xx 记 warning 日志 | `backend/app/core/hospital_resolver.py`(新) |
| extract_worker | 正则 `^([^_]+)_([0-9]{5}[0-9X])$`;批内缓存;本地 `hospital_tenant` 校验;新失败阶段 `hospital_not_found`;文件名姓名段落库 `report_info.name` | `backend/app/modules/report/extract_worker.py` |
| 重试 | `UNRETRYABLE_STAGES = ("oversize","dispatch_unmatched","hospital_not_found")` | `backend/app/modules/report/batch_service.py:263` |
| 表结构 | `report_task/report_info/chat_session.user_id` → `VARCHAR(16)`;`chat_session` 加 `name`;`platform_user` 加 `id_card_suffix` + `name` + 唯一索引 `uq_platform_user_anchor(hospital_id,name,id_card_suffix)` | `models.py`(report/chat)、`01_template_db.sql`、`003_user_id_suffix.sql`、`start.sh` |
| 认证 | register 必填 name+后六位(role='user')、拒绝重复三元组;login/JWT/`TokenResponse`/`/me` 带 name+后缀;`CurrentUser` 有 `id_card_suffix`+`name`;`user_identity()` 助手 | `backend/app/api/auth.py`、`backend/app/core/dependencies.py` |
| 报告 | `create_task(name=...)`;`process_task` 仅在 name 空时用 VLM 填;`list_reports` 双条件;存量无后缀 user 返回空列表(不泄露)、upload 400 守卫 | `backend/app/modules/report/service.py`、`router.py` |
| 下游 | user_profile / chat / AI agent 工具 / chat_planner / interp `_fetch_trend` 全部双锚定;非 user 角色回退平台 user_id | `user_profile/*`、`chat/*`、`ai/agents/{tools,chat_graph,chat_planner,interp_graph}.py` |
| 迁移 | 独立迁移脚本 + start.sh 增量 ALTER + 新建环境 template DDL | `backend/scripts/manual_migrations/003_user_id_suffix.sql`、`start.sh` else 分支 |
| 文档 | AGENTS.md 更新(failed_stage 矩阵 + 命名约定双锚定小节);旧 spec 标注废弃;新 spec/plan 含 §15 | `AGENTS.md`、两个 spec/plan 文件 |

**测试基线**:`cd backend && .venv/bin/pytest tests/ -q` → **276 passed, 2 failed**。
2 个失败均为改动前基线已存在、与本次无关:
- `tests/core/test_logging_config.py::test_monthly_rollover_renames_to_yyyymm_and_starts_new_file` —— 已知 freezegun/transformers 4.51.3 quirk(见 AGENTS.md,`freeze_time(..., ignore=["transformers"])`)
- `tests/modules/statistics/test_group_sql.py::test_high_risk_list_basic` —— 断言旧 SQL 字符串,与本次无关

---

## 3. 遗留项(需要下一个 agent / 运维处理)

### 3.1 必须做(否则功能不可用或会出错)

1. **commit**(用户明确要求:由用户自行 commit,agent 全程未 commit)。建议分 3 个 commit:
   - 后端主改动(extract_worker/batch_service/report/chat/user_profile/ai/auth/dependencies/models/resolver+测试)
   - DDL/迁移(`start.sh`/`01_template_db.sql`/`003_user_id_suffix.sql`)
   - 文档(AGENTS.md/spec/plan)
   `git status --short` 列出 39 个 M + 6 个 ?? 新文件:
   - 新文件:`hospital_resolver.py`、`003_user_id_suffix.sql`、`test_hospital_resolver.py`、`test_dependencies_user_identity.py`、`test_auth_id_suffix.py`、plan 文档
   - 注意 `.superpowers/sdd/*` 已自忽略(git 不会追踪),无需处理

2. **存量库迁移(先于后端部署)**:对**每个**存量 tenant 库(`hospital_<id>`)与 `hospital_template` 执行
   `backend/scripts/manual_migrations/003_user_id_suffix.sql`。顺序敏感:
   - `hospital_<id>` 库:`report_task/report_info/chat_session` 的 `user_id` MODIFY 为 VARCHAR(16),`chat_session` 加 `name`
   - `hospital_template` 库:`platform_user` 加 `id_card_suffix`+`name`+唯一索引
   - **务必先迁移再部署后端**,否则 login/register/报告列表 SELECT 新列会 500
   - `start.sh` 仅对 `hospital_H001` + template 自动做增量 ALTER;其他 tenant 必须手动跑脚本

3. **配置外部接口**:在 `backend/.env` 配置
   ```
   EXTERNAL_RESOLVER_URL=http://<你的接口地址>/...
   EXTERNAL_RESOLVER_TIMEOUT=10
   ```
   - 不配置(默认 `""`)→ `resolve_hospital` 一律返回 None → 所有批量文件短路 `hospital_not_found`
   - 契约暂定最简:`POST body {"id_suffix":"12345X"} → resp {"hospital_id":"H001"}`;接口文档后只改 `hospital_resolver._build_request` / `_parse_response` 两处

4. **注册终端用户**:外部系统(医院 HIS)调用 `POST /api/v1/auth/register`,role='user' 时必填
   `name` + `id_card_suffix`(5 位数字 + 末位 0-9/X)+ `hospital_id`;重复 `(hospital_id,name,id_card_suffix)` 返回 400。

### 3.2 建议做(上线前验证)

- **注册唯一性 + 中文姓名**:`test_auth_id_suffix.py` 的唯一性测试用 fake-DB SQL 字符串匹配,CI 可接受但语义不全。
  上线前对真实 MySQL 手动验一遍:唯一索引 `uq_platform_user_anchor` 生效、中文姓名 + 后六位重复注册被拒。
- **外部接口真机验证**:配好 `EXTERNAL_RESOLVER_URL` 后走一遍端到端:上传含 `张三_123456.pdf` 的 zip → 落库 → 用户登录看到自己报告。
- **start.sh else 分支 ALTER 实际执行验证**:`2>/dev/null || true` 会吞掉失败,确认三张表的 MODIFY 真的生效。

---

## 4. 待执行(本接力点明确未做的工作)

- 前端 doctor-portal 批量上传页的**命名提示文案**仍显示旧三段格式(plan Task 10 标注为"可选同步项",未做)。
  位置:`frontend/packages/doctor-portal/src/pages/BatchUploadPage.tsx`(提示卡 `<姓名>_<医院编号>_<用户编号>` → 改为 `<姓名>_<身份证后六位>`)。
- user-portal 前端无需改(报告列表走 JWT),但若产品要在界面显示登录姓名/后六位需另排。

---

## 5. 后续规划(建议的演进方向,未承诺)

1. **外部接口契约落地后**收紧 resolver:`_parse_response` 按真实响应字段适配;明确 4xx 语义(现在 401/403 也当无匹配,接口文档后可考虑 404 才 no-match、其他 4xx 走重试)。
2. **前端同步**:doctor-portal 批量上传页命名提示、失败阶段 `hospital_not_found` 的 UI 展示(红色 + 禁用重试,与 `dispatch_unmatched` 一致)。
3. **report_task 存姓名**:当前 `report_task` 无 `name` 列(姓名只落 `report_info.name`)。若统计/审计需要任务级姓名可加列,但需评估收益。
4. **chat 会话的存量数字 user_id 行**:doctor/admin 旧会话 `user_id` 是数字字符串、`name` NULL;当前按 `str(user_id)` 匹配可兼容。若未来统一清洗可做一次性数据修复。
5. **医院注册自动同步**:外部接口返回的 hospital_id 本地未注册时现在短路 `hospital_not_found`;可考虑对接 tenant 创建接口自动建租户(需产品确认,避免误建)。
6. **观察项**:`hospital_not_found` 在 extract 重试后可能重复记行(与 dispatch_unmatched 同款,非本次引入);量大可改成按 `file_path` 去重。

---

## 6. 关键约定速查(写代码/改配置前必读)

- 后六位正则:`[0-9]{5}[0-9X]`(末位可 X);文件名整正则 `^([^_]+)_([0-9]{5}[0-9X])$`
- 双锚定:`user_id`(后六位,VARCHAR(16))+ `name`(姓名,VARCHAR(50));过滤 `user_id == suffix AND name == 姓名`
- `failed_stage` 取值:`oversize` / `dispatch_unmatched` / `hospital_not_found`(不可重试)+ `parsing` / `interpretation`(可重试)
- 文件检查顺序:大小 → 文件名格式 → 医院解析
- 存量数据原则:**存量不动**,只影响新数据;旧三段命名已废弃
- 部署顺序:迁移脚本 → `.env` 配 resolver → 启动后端
- 测试命令:`cd backend && .venv/bin/pytest tests/ -q`
- 工程约束(venv/GPU/多租户)见 `AGENTS.md`,不得在 `backend/pyproject.toml` 加 vllm

---

## 7. 交接时的环境状态

- 工作区:39 M + 6 ??(全部是本次功能改动,无无关杂物)
- HEAD:`1784498`,分支 `feat/local-model-import`
- `.env` 未配置 `EXTERNAL_RESOLVER_URL`(`.env.example` 已有注释占位)
- 测试:276 passed / 2 pre-existing failed
