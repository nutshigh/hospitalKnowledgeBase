# 检后随访:随访问卷 + 需复查提醒(兼容外部 App)设计

**日期**:2026-09-07
**状态**:Draft(需求与设计决策均已与用户对齐,待 review)
**前置**:
- 身份证后六位双锚定: `docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md`
- app-login 免密登录: `docs/superpowers/specs/2026-09-01-app-login-api-key-design.md`
- 外部 App 接入文档(本次会同步更新): `docs/app-integration-guide.md`
- 多租户 DDL / 新 tenant 初始化纪律: `AGENTS.md`
- 日志收口与 logger 命名: `AGENTS.md`(新 logger 用 `app.followup`)

---

## 0. 目标与边界

### 目标
体检报告**解读完成后**,对整体风险为红/黄的报告自动做两件事(按报告各建各的):

1. **随访问卷**:给被检用户生成一份问卷实例,问题快照自**平台统一维护的一套激活通用模板**(固定模板,非 LLM),用户在外部 App(经 app-login)或 user-portal 自填,提交即办结。
2. **需复查提醒**:把该报告**黄 + 红指标**(item_name/结果/单位/参考范围/颜色)整理成一条站内通知(`recheck_reminder`),用户侧经**接口轮询**拉取展示(无第三方推送通道)。

### 使用方与 App 兼容(硬约束)
主要使用方是**外部 App**(医院 HIS / 第三方)。App 用 `POST /auth/app-login`
(app_key + 姓名 + 身份证后六位)换到 `role='user'` 的 JWT,之后调用现有 reports/chat/profile 均为
零改动复用 Bearer。**本功能的用户侧接口必须沿用同一条认证路径**:
`role='user'` + 双锚定 `(user_id=后六位, name=姓名)`,医院取 JWT,前端/App 不传。
内部端(模板维护 / 医生查看)走 admin-portal / doctor-portal 的普通登录。

### 范围内
- 平台模板库 2 张表 + 每租户 3 张表(见 §2),三处 DDL 落地(见 §6)
- 生成时机:解读 worker 内同步生成(方案 A),幂等 UNIQUE(report_id)
- 删除报告时联动清理随访/答卷/通知(`report/service.py` 级联删除扩列,见 §2.2)
- 接口:用户侧随访列表/问卷拉取/提交、通知列表/未读数/已读;管理员模板读/写;医生按报告查看随访与答卷
- 前端:admin-portal 模板维护页、user-portal 随访 tab + 问卷页(参考实现,App 照此迁移)、doctor-portal 报告详情随访区块
- 测试 + `docs/app-integration-guide.md` 增补

### 范围外(YAGNI)
- **存量报告不补建**:只对新解读完成且红/黄的报告生效
- 真推送通道(短信 / APNs / 厂商推送):未接,一律接口轮询
- 未填写的自动催办 / 定时扫表任务:不做;待随访靠前端常驻入口与 Badge 体现
- 问卷 LLM 定制、多套模板按体检类型细分、医生人工补发随访:不做
- `user_notification` 仅服务本功能;未来其它通知类别可扩展 `category`

---

## 1. 决策汇总(与用户确认)

| 维度 | 决策 |
|------|------|
| 触发 | 解读 completed 后,`overall_level ∈ {red, yellow}` 自动生成(红+黄) |
| 模板层级/维护方 | 平台级单套**激活通用模板**,admin-portal 平台管理员维护 |
| 模板内容 | 固定问题集(非 LLM),题型 single/multiple/text + options + is_required + sort |
| 快照 | 生成时把激活模板的问题**快照**进租户库实例,改模板不影响已发问卷 |
| 填写方 | 用户(被检者)自填;提交即 `completed` |
| 结果可见 | 用户自看 + 医生可查(按报告维度) |
| 多次报告 | 按报告各建各的(可追溯),不跨报告合并 |
| 「需复查指标」 | `indicator_judgment.color_level ∈ {red, yellow}`(黄+红) |
| 提醒文案 | 固定模板拼接(report_date/风险级/指标清单),结构化 JSON 落库 |
| 推送通道 | 站内通知,接口轮询(app / user-portal 拉取),无真推送 |
| 触达对象 | 被检用户本人(role='user' 双锚定) |
| 未填处理 | 一条提醒,无自动追推;pending 在用户端常驻展示 |

---

## 2. 数据模型

### 2.1 平台库 `hospital_template`(平台统一维护的模板)

**`followup_template`**

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK AI | |
| name | VARCHAR(100) NOT NULL | 模板名(如「通用检后随访」) |
| description | VARCHAR(500) NULL | |
| is_active | TINYINT NOT NULL DEFAULT 1 | 同一时刻仅 1 套激活(平台通用模板即这套;PUT 编辑的目标) |
| updated_by | BIGINT NULL | 维护人(platform_user.id,取 current_user.user_id) |
| created_at / updated_at | DATETIME | 默认 CURRENT_TIMESTAMP(updated 带 ON UPDATE) |

**`followup_template_question`**

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK AI | |
| template_id | BIGINT NOT NULL | 属主模板(逻辑 FK,不建物理外键) |
| question_type | VARCHAR(10) NOT NULL | `single` / `multiple` / `text` |
| question_text | VARCHAR(500) NOT NULL | 题干 |
| options | JSON NULL | single/multiple 的选项数组;text 为空 |
| is_required | TINYINT NOT NULL DEFAULT 1 | 必填 |
| sort_order | INT NOT NULL DEFAULT 0 | 展示顺序 |
| is_active | TINYINT NOT NULL DEFAULT 1 | 停用题目不参与生成 |
| created_at / updated_at | DATETIME | |

> 放平台库(template DB),是因为模板跨所有医院共享、由平台管理员维护(与 `platform_user` /
> `hospital_tenant` 同库)。**只读时从平台库取**;生成时快照进租户库。

### 2.2 租户库 `hospital_*`(每报告一份实例)

**`followup`**

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK AI | |
| report_id | BIGINT NOT NULL | 逻辑 FK→report_info.id;`UNIQUE(report_id)` 幂等 |
| user_id | VARCHAR(16) NOT NULL | 归属锚定(身份证后六位),生成时取自 report_info |
| name | VARCHAR(50) NULL | 归属锚定名,取自 report_info.name(非 parsed_name) |
| overall_level | VARCHAR(10) NOT NULL | red/yellow(触发时值) |
| status | VARCHAR(16) NOT NULL DEFAULT 'pending' | `pending` / `completed` |
| recheck_indicators_json | JSON NULL | 黄+红指标快照(列表),见下 |
| template_name | VARCHAR(100) NULL | 生成时模板名快照 |
| generated_at | DATETIME NULL | 生成时间(默认 CURRENT_TIMESTAMP 亦可) |
| submitted_at | DATETIME NULL | 提交即 completed 的时间 |
| created_at | DATETIME | 默认 CURRENT_TIMESTAMP |

索引:`UNIQUE KEY uq_followup_report (report_id)`;`KEY idx_followup_user (user_id, name)`。

**`followup_question`**(题目快照 + 用户答案,单表)

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK AI | |
| followup_id | BIGINT NOT NULL | 逻辑 FK→followup.id |
| question_type | VARCHAR(10) NOT NULL | 快照自模板 |
| question_text | VARCHAR(500) NOT NULL | 快照自模板 |
| options | JSON NULL | 快照自模板 |
| is_required | TINYINT NOT NULL DEFAULT 1 | 快照自模板 |
| sort_order | INT NOT NULL DEFAULT 0 | 快照自模板 |
| answer | TEXT NULL | 提交后写;single 存选项标签 / multiple 存 JSON 数组 / text 存文本 |
| answered_at | DATETIME NULL | 本小题作答时间(同一提交同刻) |

索引:`KEY idx_fq_followup (followup_id)`。

**`user_notification`**(站内通知,当前仅 `recheck_reminder`,预留扩展)

| 列 | 类型 | 说明 |
|----|------|------|
| id | BIGINT PK AI | |
| user_id | VARCHAR(16) NOT NULL | 通知对象锚定(后六位) |
| name | VARCHAR(50) NULL | 通知对象锚定名 |
| category | VARCHAR(24) NOT NULL | `recheck_reminder` |
| title | VARCHAR(200) NOT NULL | 固定模板标题 |
| content | JSON NOT NULL | 结构化负载(见 §3) |
| ref_report_id | BIGINT NULL | 关联报告 |
| ref_followup_id | BIGINT NULL | 关联随访实例 |
| is_read | TINYINT NOT NULL DEFAULT 0 | |
| read_at | DATETIME NULL | |
| created_at | DATETIME | 默认 CURRENT_TIMESTAMP |

索引:`KEY idx_un_user_created (user_id, name, created_at)`。

> **删除报告联动**:`report/service.py` 的级联删除需扩为同时删该报告的 `followup_question` /
> `followup` / `user_notification`(延续现有手动级联,不新增物理外键)。生成代码需与删除互不冲突。

### 2.3 recheck 指标快照结构与提醒内容结构

`recheck_indicators_json` / `notification.content` 用同一份结构化负载(固定模板拼接的基础):

```json
{
  "report_id": 123,
  "report_date": "2026-08-30",
  "overall_level": "yellow",
  "followup_pending": true,
  "recheck_indicators": [
    {"item_name": "甘油三酯", "result_value": "2.8", "unit": "mmol/L",
     "ref_range": "0.4-1.7", "color_level": "yellow"}
  ]
}
```

`ref_range` = `ref_range_low + "-" + ref_range_high`(缺省时 null)。标题固定模板:
`"您 {report_date} 的体检存在 {n} 项异常,请及时关注并按需复查"`。

---

## 3. 生成时序、幂等与错误处理(方案 A:解读 worker 内同步)

在 `backend/app/modules/interpretation/worker.py` 中,紧跟现有
`try_generate_comparison_summary`(`worker.py:56-66`)的成功分支后,新增第二个独立的
`try/except` 副作用钩子 `try_generate_followup(db, report_id)`,封装为只吃一个 `db` 会话
(医院库);模板库会话在函数内部自开。失败**只记日志、不炸解读、不影响 batch 进度**(与
comparison summary 同款容错)。

新模块 `backend/app/modules/followup/`(`models.py` / `schemas.py` / `router.py` /
`service.py`),service 内：

1. **守卫**：经 `db` 取 ReportInfo + ReportInterpretation;`status != 'completed'` 或
   `overall_level not in ('red','yellow')` → 返回。
2. **幂等**：`followup` 已存在 `report_id` → 返回(UNIQUE 兜底并发双击)。
3. **取模板快照**：函数内 `next(get_template_db())` 取 `is_active=1` 的 `followup_template` +
   其 `is_active=1` 题目(`sort_order` 排序),会话用毕即关;无激活模板 → 记 `app.followup`
   warning 并返回(不生成问卷也不发提醒,保持「提醒与问卷同生」语义)。
4. **构建快照**：join `indicator_judgment(interpretation_id=当前解读, color_level in red/yellow)`
   与 `report_indicator` 得 `recheck_indicators_json`;模板问题拷入 `followup_question` 快照。
5. **同一事务写**:`followup(pending)` + `followup_question` 多行 + 一条
   `user_notification(recheck_reminder, is_read=0)`;`db.commit()`。
   任一失败 → `db.rollback()` + `logging.getLogger("app.followup").exception(...)`。

要点：
- 通知与随访**同生同亡**于同一事务,不存在「只有提醒没问卷」或反之。
- 提醒落库后用户何时可见由其拉取时机决定(接口轮询),后端不做推送。
- 归属锚定(user_id/name)与 `report_date` 取自 `report_info` 行,保证与 App 双锚定一致。
- 跨院分发边界:随访与通知落在**报告所在的租户库**;App 登录解析出的医院与报告医院一致时自然可见(单医院部署不受影响)。

---

## 4. 接口契约(全部 `/api/v1` + Bearer)

### 4.0 通用
- `role='user'`:医院取 JWT;数据一律 `user_id == id_card_suffix AND name == name` 双锚定。
  **App 的 app-login JWT 零改动可用**(与 reports/chat 同构)。
- 存量 `role='user'` 无后六位 → 双锚定 `(None, None)` → 返回空列表,不报 404/500(App 不会误判 token 失效)。
- 越权/无后六位遵循现有 reports 的“空结果”语义,不泄露他人数据。

### 4.1 用户侧(外部 App + user-portal 共用)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/followup/center` | 我的随访列表(新→旧,分页 page/page_size),每项含 id/status/overall_level/report_date/recheck_indicators 快照/template_name/generated_at/submitted_at;附 `has_pending` 供首屏判定 |
| GET | `/followup/{id}` | 问卷表单:逐题 id/question_type/question_text/options/is_required/sort_order;completed 时带 answer。归属校验 404 |
| POST | `/followup/{id}/submit` | body `{answers:[{question_id, answer}]}`;校验归属(404)/缺必填、选项非法、答案超长(400)/重复提交(400);成功置 completed+submitted_at,写每题 answer/answered_at |
| GET | `/notifications` | 我的通知列表(新→旧,分页);`?unread_only=true` 可选 |
| GET | `/notifications/unread-count` | `{unread_count:int}` — App 红点/角标轮询的轻量入口 |
| POST | `/notifications/{id}/read` | 单条已读,返回 `{status:'ok'}` |
| POST | `/notifications/read-all` | 全部已读,返回 `{status:'ok'}` |

模块命名:`followup`(列表/问卷/提交)与 `notifications` 两个 router 均挂 `/api/v1`,在
`main.py` 里注册为 `followup_router`(prefix `/followup`)、`notification_router`(prefix
`/notifications`)——沿用现有模块单 router + main 注册 prefix 的方式。

错误码沿用 App 集成文档约定:400 参数/重复提交;401 token 失效(重调 app-login);404 无权限/不存在;503 不可用。

### 4.2 平台管理员(admin-portal,平台库)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/followup/template` | 读激活模板(id/name/description/questions[])。无则返回空模板结构 |
| PUT | `/followup/template` | body `{name, description, questions:[{id|null, question_type, question_text, options, is_required, sort_order, is_active}]}` **全量替换**;id 为空新增、有 id 更新。目标恒为激活模板:无激活模板行则新建一条并激活;若平台只该一条通用模板则原地更新。`questions` 为空 → 400(激活模板不允许空题目)。其它非激活模板行不动(平台只此一套通用模板,其余历史行忽略) |

鉴权:`require_role("admin")` + `Depends(get_template_db)`。平台管理员 = `role='admin'`(平台惯例为
`hospital_id IS NULL`,代码不强校验,与现有 group/tenants 端点一致)。不设“每医院各自模板”。

### 4.3 医生(doctor-portal,租户库)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/followup/by-report/{report_id}` | 该报告是否有随访、status、overall_level、user/name、generated/submitted_at、recheck 指标快照、逐题 question+answer;无则 404。仅 doctor/admin |

医生在医院范围内以 **report 维度**查看(非双锚定;沿用现有非 user 角色看全医院报告的权限模型)。

---

## 5. 前端

### 5.1 admin-portal(平台超管维护模板)
- 新路由 `/followup-template`「随访问卷模板」,放入平台 admin 菜单。
- 页面:`GET /followup/template` 载入;问题列表编辑(新增/删除/上移/下移/题型切换/选项编辑/必填开关/停用);
  「保存」→ `PUT /followup/template` 全量替换;空题目列表保存被后端 400 拦截并在表单提示。

### 5.2 user-portal(C 端 H5,App 迁移的参考实现)
- **新增第 4 个底部 tab「随访」**(铃铛图标),`Layout.tsx`(现 tab 定义处)加入;tab 红点取
  `notifications/unread-count`:随访中心页内轮询该值(进入即刷 + 每 30s),并写入全局 store 供
  Layout 的 Badge 显示(页面卸载停止轮询)。
- `/followup`「随访中心」:顶部 `待随访(pending)/ 已填写(completed)` 切换;卡片=报告日期 +
  overall_level 色标 + 需复查指标清单(结果/单位/参考范围/颜色)+ 状态;pending 卡带「去填写」。
  已读/未读通知统一在此 tab 内展示,读时调 `/notifications/{id}/read`。
- `/followup/:id`「问卷填写」:按题渲染(radio/checkbox/textarea),必填本地校验 →
  `POST submit`;成功后返回列表并提示;completed 态只读回看答案。
- API 层:user-portal 现有 `api` client(axios,Bearer)直接可用;页面调用与 reports 同风格。

### 5.3 doctor-portal
- `ReportDetailPage` 增「随访问卷」区块:`GET /followup/by-report/{report_id}`;
  未生成显示「该报告未触发随访」;有则显示状态/风险级/填写时间/逐题答案(只读)。

---

## 6. 多租户 DDL 落地(三处一致)

新表共 5 张:平台库 2(`followup_template`、`followup_template_question`)、租户库
3(`followup`、`followup_question`、`user_notification`)。按 `AGENTS.md`「新 tenant 初始化必读」,
三处同步：

1. **`infra/mysql/init/01_template_db.sql`**(平台库模板):补 2 张 `CREATE TABLE IF NOT EXISTS`。
2. **`start.sh` DDL 块**(hospital_H001 自动初始化):`CREATE TABLE IF NOT EXISTS` 补 3 张租户表。
3. **`infra/mysql/init/02_hospital_created.sql`** 存储过程 `create_hospital_database`:在表序列中补
   3 张租户表(新 tenant `CALL` 即带)。
4. **存量库手工迁移 `backend/scripts/manual_migrations/006_followup.sql`**:
   - 平台库:建 2 张模板表(`hospital_template`,直接执行);
   - 5 个存量租户库(`hospital_H001/H002/H003/H004/hospital_1`):建 3 张表。
   - 备注:本机 MySQL 8 不支持 `ADD COLUMN IF NOT EXISTS`(见 AGENTS.md),用纯 `CREATE TABLE IF NOT EXISTS` 即可(全新表无增量列)。

**worker/start 约束**:不改动 RabbitMQ、不新增 worker 进程(方案 A);无需动 `start.sh` 的 worker
启动/日志部分。新 logger `app.followup` 自动收口到 `/data/logs/app.log`。

---

## 7. 测试(backend,照 `tests/` 现有 pytest 风格)

- `tests/followup/test_service.py`:
  - 生成:red/yellow 触发且生成问卷快照+通知;green 跳过;重复调用幂等;无激活模板跳过(记日志不报错);
    模板问题 `is_active=0` 不入快照、`sort_order` 排序正确。
  - 快照稳定性:生成后再改模板,已生成实例的题目/文案不变。
  - 提交:归属校验、必填缺失 400、选项非法 400、重复提交 400、成功后 `status=completed` 且答案落库。
- `tests/followup/test_app_router.py`(App 兼容):
  - app-login 换 token 后 `GET /followup/center`、`/followup/{id}`、`/notifications*` 走双锚定,
    只能看到本人;他人随访/通知 404/不可见;无后六位用户返回空集(不 500/401)。
  - admin 端点 `GET/PUT /followup/template` 非 admin 403;医生 `by-report` 越界 report 404。
- `tests/followup/test_interpretation_hook.py`:mock `run_interpretation_agent` 完成后,
  worker 侧触发生成;异常被吞且不影响解读完成(与 comparison summary 同模式)。
- 通知:unread-count / read / read-all 行为。
- 前端无单测基建(仓库无 jest/vitest),以 `tsc` build + 手工清单验证(沿用仓库惯例)。

---

## 8. 上线检查单

- 平台库 + 5 存量租户库跑 `006_followup.sql`;`01_template_db.sql` / `02_hospital_created.sql` /
  `start.sh` DDL 三处已同步(新 tenant 由 create_hospital_database 带出)。
- 平台管理员在 admin-portal 配好一套激活通用模板(后续新解读才会套用)。
- backend 重启;`report`/`interpretation` worker 无新进程需求。
- `docs/app-integration-guide.md` 增补:`/followup/*`、`/notifications/*` 契约 + 与 user-portal
  页面映射表加「随访中心 / 问卷填写」;错误码表补 400 重复提交语义。
- 手工验证:上传/触发一份黄或红解读 → `reports` 完成 → user-portal 随访 tab 出现提醒与问卷 → 填写
  提交 → 医生报告详情可见;绿报告不产生随访与通知。
