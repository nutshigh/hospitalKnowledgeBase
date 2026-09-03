# 外部 App 接入文档：用户报告 / 聊天 / 画像接口

> 本文档供**外部 App（医院 HIS / 第三方系统）**的后端开发 Agent 阅读，用于把现有
> `user-portal`（C 端体检报告查询）的全部功能迁移到 App。
>
> 核心路径：**app-login 免密登录** —— 外部系统持应用级密钥 `APP_API_KEY`，用
> `姓名 + 身份证后六位` 为任意用户换一个与普通登录完全一致的 JWT，之后对
> `/api/v1/reports/*`、`/api/v1/chat/*`、`/api/v1/profile/*`、`/api/v1/interpretations/*`
> 的调用**零改动复用现有 Bearer 认证**。
>
> 关联文档：
> - 双锚定设计：`docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md`
> - app-login 设计：`docs/superpowers/specs/2026-09-01-app-login-api-key-design.md`
> - 外部接口(baUser)：`docs/baUser-open-api.md`
> - 全部接口参考：`docs/backend-api.md`
> - 工程约束：`AGENTS.md`

---

## 1. 接入概览

```
┌────────────────────┐   HTTPS    ┌──────────────────────────────────────────┐
│  外部 App / HIS     │ ─────────▶ │  体检系统后端 (:8000, /api/v1)            │
│  ┌───────────────┐  │   app-login│  ├── auth/app-login (换 token)          │
│  │ 登录模块       │  │  Bearer    │  ├── reports/*    报告列表/详情/上传/删除 │
│  │ 报告列表/详情  │  │           │  ├── interpretations/*  解读详情          │
│  │ 上传报告       │  │           │  ├── chat/*       会话 CRUD + SSE 消息   │
│  │ AI 健康咨询    │  │           │  └── profile/*    健康画像                │
│  │ 健康档案       │  │           │                                          │
│  └───────────────┘  │           │   └── hospital_<id> 库(按 JWT 隔离)      │
└────────────────────┘            └──────────────────────────────────────────┘
```

**信任模型（重要）**：持有 `APP_API_KEY` 的系统可代任意 `(姓名, 后六位)` 签发 user 级
token，等于可访问该用户的全部报告与聊天记录。**必须 TLS + key 保密，仅授予可信系统。**

---

## 2. 环境与配置

### 2.1 当前测试环境（已就绪，2026-09-02 验证）

| 项 | 值 |
|----|----|
| 后端 Base URL | `http://localhost:8000/api/v1` |
| **`APP_API_KEY`** | **`test-app-key-2026`** |
| `APP_LOGIN_TOKEN_EXPIRE_MINUTES` | `10080`（7 天） |
| resolver(BaseURL) | `http://localhost:8082/snowyApi` |
| resolver 接口路径 | `/biz/baUserOpen/searchUser`（后端拼装，App 无需关心） |
| 已注册租户 | `hospital_id=1`（市人民医院） |
| 已存在的 baUser | 张三(`011234`)、李四(`151234`) |

### 2.2 生产/测试环境申请清单（对接医院方要拿到）

1. 后端网关地址（生产如 `https://<域名>/api/v1`）
2. **`APP_API_KEY`**（一个全局 key，由医院方在 `backend/.env` 配置后提供）
3. baUser 中预置用户的姓名 + 身份证后六位（App 侧不需要，由后端 resolver 解析）

### 2.3 前置条件（医院侧已就绪，App 无需处理）

- 存量库已跑 `003_user_id_suffix.sql` 迁移
- 对应 orgId 已在本地注册为租户（`hospital_id == str(orgId)`）
- 用户三元组 `(hospital_id, name, id_card_suffix)` 可自动注册

---

## 3. 认证：app-login 免密登录

### 3.1 接口

```
POST /api/v1/auth/app-login
Content-Type: application/json
```

请求体：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `app_key` | string | 是 | 与医院方 `APP_API_KEY` 一致 |
| `name` | string | 是 | 姓名（与报告文件名姓名段一致，≤50 字符） |
| `id_card_suffix` | string | 是 | 身份证后六位，正则 `^[0-9]{5}[0-9X]$` |

### 3.2 成功响应（200）

与 `/auth/login` 完全相同的 `TokenResponse`：

```json
{
  "access_token": "<JWT>",
  "token_type": "bearer",
  "user_id": 12,
  "role": "user",
  "hospital_id": "1",
  "id_card_suffix": "011234",
  "name": "张三"
}
```

### 3.3 错误响应

| 场景 | 状态码 | detail |
|------|--------|--------|
| `APP_API_KEY` 未配置或 `app_key` 不匹配 | 401 | Invalid app key |
| `name` 为空 | 400 | name required |
| `name` 超长 | 400 | name too long (max 50) |
| `id_card_suffix` 为空或非法 | 400 | id_card_suffix required (5 digits + digit or X) |
| resolver 解析不出医院（无匹配/本地未注册） | 401 | 无法匹配用户医院 |
| resolver 宕机 | 503 | resolver 不可用 |
| 用户被停用 | 401 | 用户已停用 |

### 3.4 行为说明

- **自动注册**：三元组 `(hospital_id, name, id_card_suffix)` 不存在时自动建
  `platform_user` 行（username=`app_<hid>_<name>_<suffix>`、随机密码不可密码登录），
  二次调用命中已有行，天然幂等。App 无需关心用户是否已注册。
- **token 有效期**：`APP_LOGIN_TOKEN_EXPIRE_MINUTES`（默认 7 天）。到期后 401，
  App 侧重调 app-login 换新 token 即可（幂等）。

### 3.5 curl 示例

```bash
curl -X POST http://localhost:8000/api/v1/auth/app-login \
  -H 'Content-Type: application/json' \
  -d '{"app_key":"test-app-key-2026","name":"张三","id_card_suffix":"011234"}'
```

---

## 4. 通用约定

- **鉴权**：所有接口 `Authorization: Bearer <access_token>`。
- **Base URL**：`/api/v1`（见 2.1）。
- **token 失效**：任何接口返回 401 且 detail 为 `Invalid or expired token` 时，重调
  app-login 换新 token 重试一次。
- **医院隔离**：`hospital_id` 从 JWT 取，前端/App **不要传**。数据按用户所在医院库隔离。
- **双锚定**：`role='user'` 时后端用 `user_id == 后六位 AND name == 姓名` 双条件过滤
  报告/会话。因此一个用户**只能看到/操作自己的数据**。

---

## 5. 体检报告 `reports`

前缀：`/api/v1/reports`（需 Bearer）

### 5.1 报告列表

```
GET /api/v1/reports?page=1&page_size=20
```

Query：`page`(≥1)、`page_size`(1–100)。

响应 `{ items, total, page, page_size }`，`items` 每项字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 报告 ID |
| task_id | int\|null | 解析任务 ID |
| name | string\|null | 展示姓名(PDF 解析出的真实姓名;为空回退归属锚定名) |
| gender | string\|null | 性别 |
| age | int\|null | 年龄 |
| report_date | date\|null | 报告日期 |
| check_type | string\|null | 检查类型 |
| unit_name | string\|null | 单位名称 |
| task_status | string\|null | 任务状态（queued/parsing/completed/failed） |
| interp_status | string\|null | 解读状态（pending/processing/completed/failed） |
| overall_level | string\|null | 整体判定（red/yellow/green/null） |
| created_at | datetime\|null | 创建时间 |

> user 角色仅返回本人报告；若用户无后六位（存量），返回空列表 `{items:[],total:0,...}`。

### 5.2 报告详情

```
GET /api/v1/reports/{report_id}
```

响应 `ReportDetailResponse`（字段见 `docs/backend-api.md §6.4`），含 `indicators` 数组：
`item_name`、`item_name_standard`、`item_code`、`result_value`、`unit`、
`ref_range_low`、`ref_range_high`、`category`。

### 5.3 单份报告上传

```
POST /api/v1/reports/upload
Content-Type: multipart/form-data
```

Form 字段：`file`（`.pdf/.docx/.doc/.jpg/.jpeg/.png`，≤20MB）。

响应 `TaskStatusResponse`：

| 字段 | 类型 | 说明 |
|------|------|------|
| task_id | int | 解析任务 ID |
| status | string | queued/parsing/... |
| error_message | string\|null | 错误信息 |
| created_at | datetime | 创建时间 |
| completed_at | datetime\|null | 完成时间 |

**上传后轮询**：App 须轮询 `GET /reports/tasks/{task_id}` 直到 `status == completed`，
再轮询 `GET /interpretations/{report_id}` 直到解读完成。间隔建议 ≥10s。

### 5.4 任务状态

```
GET /api/v1/reports/tasks/{task_id}
```

响应同上 `TaskStatusResponse`。

### 5.5 删除报告

```
DELETE /api/v1/reports/{report_id}
```

响应 `{ "status": "deleted" }`。级联删除关联解读/指标/会话/消息。

---

## 6. 解读 `interpretations`

前缀：`/api/v1/interpretations`（需 Bearer）

### 6.1 解读详情

```
GET /api/v1/interpretations/{report_id}
```

响应 `InterpretationResponse`：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 解读行 ID |
| report_id | int | 报告 ID |
| overall_level | string\|null | red/yellow/green |
| red_count / yellow_count / green_count | int | 红/黄/绿指标计数 |
| status | string | pending/processing/completed/failed |
| summaries | object | 见下 |
| references | list | 引用条目 |
| quality_note | string\|null | 质控说明 |
| indicators | list | 指标判定（含 color_level/deviation/explanation/suggestion） |
| created_at / completed_at | datetime | 时间 |

`summaries` 字段：`overall_summary`、`abnormal_focus`、`trend_note`、`suggestions`、`risk_alert`。

---

## 7. 聊天 `chat`

前缀：`/api/v1/chat`（需 Bearer）

### 7.1 会话列表

```
GET /api/v1/chat/sessions
```

响应 `list[SessionResponse]`：`id`、`user_id`、`report_id`、`title`、`created_at`、`updated_at`。

### 7.2 创建会话

```
POST /api/v1/chat/sessions
Content-Type: application/json
```

请求体：`{ "report_id": <int|null> }`（可选，关联报告）。

响应：`SessionResponse`。App 拿到 `id` 后即可发消息。

### 7.3 会话详情 / 更新 / 删除

```
GET    /api/v1/chat/sessions/{session_id}
PATCH  /api/v1/chat/sessions/{session_id}   body: { "report_id": <int|null> }
DELETE /api/v1/chat/sessions/{session_id}
```

PATCH 响应：`{ "status": "ok", "report_id": <id> }`；DELETE 响应：`{ "status": "deleted" }`。

### 7.4 消息列表

```
GET /api/v1/chat/sessions/{session_id}/messages
```

响应 `list[MessageResponse]`：`id`、`session_id`、`role`（user/assistant）、`content`、
`knowledge_refs`（引用）、`created_at`。

### 7.5 发送消息（SSE 流式）

```
POST /api/v1/chat/sessions/{session_id}/messages
Content-Type: application/json
```

请求体：`{ "content": "<1–4000 字符>" }`

响应：`text/event-stream`，逐行事件：

```
event: <event_type>
data: <json>

```

| event | data | 说明 |
|-------|------|------|
| `token` | `{ "content": "..." }` | 流式文本增量，App 应拼接显示 |
| `tool_status` | `{ "tool": "...", "status": "start"/"end" }` | 工具调用状态（可忽略） |
| `structured` | `{ certainty, certainty_reason, citations, annotated_text? }` | 结构化数据（置信度+引用） |
| `done` | `{ "message_id": <int> }` | 完成，结束 |
| `error` | `{ "message": "..." }` | 错误，结束 |

**移动端注意**：
- 需支持 HTTP 长连接/流式读取（fetch ReadableStream 或 SSE client），超时设长
  （AI 生成通常几十秒到分钟级），或对连接空闲做心跳探测。
- 收到 `done` 后即可关闭连接；断线可重新拉 `7.4` 消息列表补齐（后端已落库）。

---

## 8. 健康画像 `profile`

前缀：`/api/v1/profile`（需 Bearer）

### 8.1 健康概览

```
GET /api/v1/profile/overview
```

响应：`{ user_summary, indicator_trends, abnormal_distribution }`（无数据时各为空/None）。

### 8.2 报告对比

```
GET /api/v1/profile/compare?report_id=<required>&baseline_id=<optional>
```

### 8.3 AI 总结

```
GET /api/v1/profile/ai-summary?report_id=<required>&baseline_id=<required>
```

响应：`{ "ai_summary": "<文本>", "cached": <bool> }`。

---

## 9. 其它

### 9.1 校验 token

```
GET /api/v1/auth/me
```

响应：`TokenResponse`（`access_token` 为空串，其余字段含 user 信息）。可用于 App 启动时
校验本地 token 是否仍有效。

---

## 10. 与 user-portal 页面的功能映射

| user-portal 页面 | 调用的接口 |
|------------------|-----------|
| LoginPage | `POST /auth/app-login`（替代原 `/auth/login`） |
| HomePage（我的报告） | `GET /reports`（10s 轮询未完成任务） |
| UploadPage | `POST /reports/upload` + `GET /reports/tasks/{id}` 轮询 |
| ReportDetailPage | `GET /reports/{id}`、`GET /interpretations/{id}`、`GET /reports/tasks/{id}`、`DELETE /reports/{id}`、会话创建/消息 |
| ChatPage / ChatPanel | `GET /chat/sessions`、`POST /chat/sessions`、`GET /chat/sessions/{id}/messages`、`POST /chat/sessions/{id}/messages`(SSE) |
| ProfilePage | `GET /profile/overview`、`GET /profile/compare`、`GET /profile/ai-summary` |

---

## 11. 错误码速查

| 状态码 | 含义 | App 处理建议 |
|--------|------|-------------|
| 200 | 成功 | — |
| 400 | 参数错误 / 存量用户无后六位 | 提示用户/修正入参 |
| 401 | key 错、token 失效、resolver 无匹配 | 重调 app-login 换 token |
| 403 | 角色不允许（不应发生，app-login 固定 user） | 联系医院方 |
| 404 | 报告/会话不存在 | 提示后刷新列表 |
| 503 | resolver/后端临时不可用 | 退避重试 |

---

## 12. 上线检查单

### 医院方
- [ ] 存量库迁移 `003_user_id_suffix.sql` 已执行
- [ ] baUser 各 orgId 已在本地注册租户（`hospital_id == str(orgId)`）
- [ ] `backend/.env` 已配置 `APP_API_KEY` + `EXTERNAL_RESOLVER_URL`
- [ ] backend + 三个 worker 已重启为新代码，`/health` 全 UP

### App 方
- [ ] `POST /auth/app-login` 用正确 key + 姓名 + 后六位换到 token
- [ ] 错误 key → 401；非法后六位 → 400
- [ ] `GET /reports` 返回该用户本人报告
- [ ] 上传报告 → 轮询任务 → 轮询解读 → 详情可见
- [ ] 建会话 → SSE 发消息 → 收到 token/done 事件
- [ ] `GET /profile/overview` 返回画像
- [ ] token 到期后重调 app-login 幂等换新
