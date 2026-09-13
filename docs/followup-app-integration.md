# 检后随访：外部 App 接入接口文档

> **读者**：外部 App（医院 HIS / 第三方系统）的后端与前端 Agent。本文档只描述**随访 + 复查提醒**这一块
> 如何接入；登录/鉴权与报告、聊天、画像等基础接口见 `docs/app-integration-guide.md`（其中 §8.4 也有本功能
> 的摘要，本文档是它的展开版）。
>
> 本功能已上线代码版本见 commit `0125621` 起（分支 `feat/user-notification`），后端路由
> `backend/app/modules/followup/router.py`、service `backend/app/modules/followup/service.py`，
> 数据库：平台模板表在 `hospital_template`，随访/通知实例表在各自 `hospital_<id>` 租户库。
>
> **改本功能后端时**：改完需同步更新本文档 + `docs/app-integration-guide.md` §8.4 + AGENTS.md「检后随访表」
> 一节，并跑 `backend/tests/followup`。

---

## 1. 功能语义（App 必须先理解）

体检报告**解读完成后**，若该报告 `overall_level ∈ {red, yellow}`，系统会自动做两件事（**一次性的、按报告各建各的**）：

1. **随访问卷**：按**平台管理员维护的一套激活通用模板**生成一份问卷实例（题目、选项是**生成时的快照**，后续改模板不影响已生成问卷）。
2. **复查提醒**：把该报告**黄/红指标**整理成一条站内通知（`category=recheck_reminder`），由用户填写状态联动。

关键边界：

| 特性 | 行为 |
|------|------|
| 触发条件 | 仅解读 `completed` 且红/黄；`green` 报告**不生成** |
| 存量报告 | **不回填**——只对之后完成的解读生效 |
| 幂等 | 每 `report_id` 只生成一次（`UNIQUE(report_id)`） |
| 推送 | **无真实推送**，后端只落库；App 用接口轮询（见 §6） |
| 办结 | 用户提交问卷即 `completed`，**不可重复提交** |
| 提醒与问卷 | 同事务一起生成；无激活模板时两者都不生成（后端记 `app.followup` 日志） |
| 删除报告 | `DELETE /reports/{id}` 会级联清理该报告的问卷/答卷/通知，App 侧列表可能因此变少 |

---

## 2. 身份与鉴权（沿用 app-login）

- 登录：`POST /api/v1/auth/app-login`，body `{app_key, name, id_card_suffix}` → 返回与普通登录一致的 JWT
  （`role='user'`，默认 7 天）。详见 `docs/app-integration-guide.md` §3。
- 之后所有接口 `Authorization: Bearer <token>`。
- **不要传 hospital_id**：医院从 JWT 取，后端据此打开对应租户库。
- **数据隔离 = 双锚定**：user 端点一律按 `user_id==身份证后六位 AND name==姓名` 过滤，App 只能看到本人数据。
- 存量 user（登录时无后六位）→ `/followup/*` 与 `/notifications/*` 返回**空集/0**（200），不是 401/500。
- token 失效（401 `Invalid or expired token`）→ 重调 app-login 换 token 重试一次（幂等）。

---

## 3. 后端接口清单

Base URL：`/api/v1`（同 app-integration-guide 2.1）。

### 3.1 用户侧随访 `followup`（role=user）

#### `GET /followup/center` — 我的随访列表

Query：`page`(≥1，默认 1)、`page_size`(1-100，默认 20)。新→旧。

```json
{
  "items": [
    {
      "id": 3,
      "report_id": 42,
      "status": "pending",            // pending | completed
      "overall_level": "yellow",
      "report_date": "2026-09-01",    // 体检日期(从报告取)
      "recheck_indicators": [         // 黄/红指标快照
        {"item_name": "甘油三酯", "result_value": "2.8", "unit": "mmol/L",
         "ref_range": "0.4-1.7", "color_level": "yellow"}
      ],
      "template_name": "通用检后随访",
      "generated_at": "2026-09-01T10:00:00",
      "submitted_at": null
    }
  ],
  "total": 1, "page": 1, "page_size": 20,
  "has_pending": true
}
```

`recheck_indicators` 字段：`item_name`/`result_value`/`unit`/`ref_range`(可 null)/`color_level`。

#### `GET /followup/{followup_id}` — 问卷表单

非本人或不存在 → 404。

```json
{
  "id": 3, "report_id": 42, "status": "pending",
  "overall_level": "yellow", "template_name": "通用检后随访",
  "recheck_indicators": [ /* 同上 */ ],
  "generated_at": "2026-09-01T10:00:00", "submitted_at": null,
  "questions": [
    {"id": 1, "question_type": "single", "question_text": "您是否已经了解本次体检报告中的异常结果？",
     "options": ["完全了解","大致了解","不太了解","需要医生进一步解释"],
     "is_required": true, "sort_order": 1, "answer": null},
    {"id": 4, "question_type": "text", "question_text": "如有不适，请补充描述…",
     "options": [], "is_required": false, "sort_order": 4, "answer": null}
  ]
}
```

`question_type`：`single`（单选，radio）/ `multiple`（多选，checkbox）/ `text`（文本）。
已完成时每题带 `answer`（见 §5 答案语义）。渲染只依赖当前 GET 返回的题目，**不要**缓存历史模板结构。

#### `POST /followup/{followup_id}/submit` — 提交答卷

Body：

```json
{
  "answers": [
    {"question_id": 1, "answer": "大致了解"},
    {"question_id": 6, "answer": ["偶尔"]},   // multiple 传数组
    {"question_id": 4, "answer": "早晨偶尔头晕"}  // text 传字符串
  ]
}
```

返回 200 = 提交后的问卷详情（同 `GET /followup/{id}`，`status=completed`，带答案）。
选项与答案语义见 §5。**校验失败统一 400**（detail 为中文，App 可直接展示）：

| 场景 | detail |
|------|--------|
| 问卷不存在/非本人 | 404（不是 400） |
| 已提交再提交 | `问卷已提交,不能重复提交` |
| answers 不是数组 | `answers 必须是数组` |
| 某项缺 `question_id` | `answers 每项需含 question_id` |
| 同一 `question_id` 出现两次 | `题目 {id} 重复提交` |
| 含不存在的题目 | `包含不存在的题目` |
| 必填题未答 | `题目「{题干}」为必填` |
| 单选/多选值不在选项内 | `题目「{题干}」选项不合法` |
| 文本题不是字符串 | `题目「{题干}」需文本回答` |
| 文本超 2000 字 | `题目「{题干}」回答过长` |

### 3.2 用户侧通知 `notifications`（role=user）

#### `GET /notifications` — 我的通知列表

Query：`page`、`page_size`、`unread_only`(true/false)。新→旧。

```json
{
  "items": [
    {"id": 5, "category": "recheck_reminder",
     "title": "您 2026-09-01 的体检存在 1 项异常,请及时关注并按需复查",
     "content": {
       "report_id": 42, "report_date": "2026-09-01", "overall_level": "yellow",
       "followup_pending": true,
       "recheck_indicators": [ {"item_name": "甘油三酯", "result_value": "2.8",
                                 "unit": "mmol/L", "ref_range": "0.4-1.7",
                                 "color_level": "yellow"} ]
     },
     "is_read": false,
     "ref_report_id": 42, "ref_followup_id": 3,
     "created_at": "2026-09-01T10:00:00"}
  ],
  "total": 1, "page": 1, "page_size": 20
}
```

`content` 的键是稳定的：`report_id`/`report_date`/`overall_level`/`followup_pending`/`recheck_indicators`。
App 可据此把指标清单直接渲染成提醒卡片（无需再查报告接口）。

#### `GET /notifications/unread-count` — 红点轮询

```json
{ "unread_count": 1 }
```

#### `POST /notifications/{notification_id}/read` — 单条已读

不存在/非本人 → 404；成功 `{"status": "ok"}`。幂等（已读再标仍 ok）。

#### `POST /notifications/read-all` — 全部已读

`{"status": "ok"}`。

### 3.3 内部接口（App **不要**调，仅供医生/平台）

| 接口 | 角色 | 说明 |
|------|------|------|
| `GET /followup/by-report/{report_id}` | doctor/admin | 医生按报告查随访状态与答卷 |
| `GET|PUT /followup/template` | admin | 平台维护激活问卷模板 |

App 若用非 user 角色会 403；请只用 app-login 的 user token。

---

## 4. 状态流转

```
解读completed(红/黄) ──(自动,一次性)──▶ followup(status=pending) + user_notification(is_read=0)
                                              │
用户打开问卷 GET /followup/{id}               │
用户提交 POST /followup/{id}/submit           │
                                              ▼
                                   followup(status=completed)   ← 不可再提交
```

- 问卷 `pending` 期间 App 可反复 GET；`completed` 后 GET 用于回看答案。
- 通知只生成一条，不随问卷完成自动隐藏/消失；`is_read` 由 App 显式调用标记（见 §6）。

---

## 5. 字段与答案语义（易错点）

- **选项即值**：single 的 answer 必须是选项字符串原文；multiple 的 answer 是选项字符串**数组**；text 是字符串。
- **必填判定**：single/multiple/text 都**不允许空**（`""`/`[]`/缺省都算未答）→ 400 必填。可选题可传 `""`/`[]` 或省略该项，落库为 null。
- 题目、选项、`is_required`、`sort_order` 是**生成时快照**，与当前平台模板无关；改模板不影响进行中/已完成的问卷。
- 提交后 `answer` 存储语义：single→字符串、multiple→JSON 数组（读取时已还原为数组）、text→字符串；未答→null。
- `is_required`/`is_read` 布尔化返回；时间字段为无时区 ISO 串（`YYYY-MM-DDTHH:MM:SS`，服务端本地时区），`report_date` 为 `YYYY-MM-DD`，可为 null。

---

## 6. 轮询与“推送”约定（无真推送）

后端不做任何推送（无短信/APNs/厂商通道）。App 端建议：

1. App 进入前台 / 每次打开随访相关页面：调 `GET /notifications/unread-count` 刷新角标；页面存活期间**每 30s 轮询一次**（与 user-portal 参考实现一致）。
2. 角标 = `unread_count`；“有待填写问卷”可用 `/followup/center` 的 `has_pending`。
3. 点开某条提醒进入问卷/详情后：调 `POST /notifications/{id}/read`（或 `read-all`）把该项标已读。
4. 401 时重调 app-login 换 token 后重试；503/网络错误退避重试。

---

## 7. 前端参考实现（user-portal，App 照此迁移）

仓库 C 端 H5 已实现同款功能，可作 App 的页面/交互参考（**当前分支以 `.tsx` 为准**，无旧 `.js` 副本）：

| App 需要的页面/能力 | user-portal 参考实现 | 调用的后端接口 |
|---|---|---|
| 未读角标 + 30s 轮询 | `src/stores/followupStore.ts`（count/refresh）；`src/components/Layout.tsx`（第 4 个“随访”tab + Badge） | `GET /notifications/unread-count` |
| 随访中心（待随访/已填写/提醒通知） | `src/pages/FollowUpCenterPage.tsx` | `GET /followup/center`、`GET /notifications`、`POST /notifications/{id}/read`、`POST /notifications/read-all` |
| 问卷填写（single=radio / multiple=checkbox / text=textarea，本地必填校验） | `src/pages/FollowUpQuestionnairePage.tsx` | `GET /followup/{id}`、`POST /followup/{id}/submit` |
| 路由 | `src/router.tsx`（`/followup`、`/followup/:id`，AuthGuard） | — |

> 交互细节示例：pending 卡片“去填写”→ 问卷页；提交成功后回列表刷新并 toast；completed 问卷只读。
> App 如自行开发 UI，可按 §3 契约 + 上表语义等价实现即可，不要求复用这些组件。

---

## 8. 联调检查清单

1. 平台管理员已配置激活模板（`hospital_template.followup_template` 有一行 `is_active=1` + 题目若干）。
2. 后台已跑 `backend/scripts/manual_migrations/006_followup.sql`（平台库 2 张模板表 + 各租户库 3 张业务表）。
3. app-login 拿 token → `GET /followup/center` 空列表正常；`unread-count` 为 0。
4. 触发一份**新**红/黄报告的解读完成 → `unread-count` 变 1；`center` 出现 `pending`，`recheck_indicators` 非空、`report_date` 正确。
5. 打开 `GET /followup/{id}` → 逐题渲染；单选用 `options` 里的字符串提交；多选传数组。
6. 必填缺失 / 值不在选项 / 空多选 / 重复提交 → 400 中文提示；提交成功 → `status=completed`、带答案。
7. 通知标已读后 `unread_count` 递减；`read-all` 归零。
8. 绿报告解读完成不产生任何随访/通知。

---

## 9. 变更同步纪律

- 改动本功能的**接口路径/参数/字段/错误码/校验**时，三处文档必须同步：本文档、`docs/app-integration-guide.md` §8.4（含 §10 映射表、错误码、检查单）、`AGENTS.md`「检后随访表」。
- 平台模板题目改版只影响**之后**生成的问卷（快照），但**这是产品行为而非接口变更**，App 无需改。
- 后端回归：`cd backend && .venv/bin/python -m pytest -q tests/followup`。
