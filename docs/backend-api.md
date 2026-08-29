# 后端接口文档

> 权威来源:`backend/app/main.py`(路由注册)与各模块 `router.py` / `schemas.py`。本文档描述 REST API 全部端点。

---

## 1. 总览

### 1.1 基本信息

| 项 | 说明 |
|----|------|
| Base URL | `http://<host>:8000`(经 APISIX 网关统一暴露为 `/api/v1/*`) |
| 协议 | HTTP/1.1,JSON;文件上传用 `multipart/form-data`;聊天用 SSE(`text/event-stream`) |
| 鉴权 | `Authorization: Bearer <JWT>`(见 §1.3) |
| 多租户 | 每家医院独立 MySQL 库,`hospital_id` 从 JWT payload 或请求上下文获取,前端不显式传 |

### 1.2 路由前缀汇总

| 前缀 | tags | 说明 |
|------|------|------|
| `/api/v1` | health | 健康检查 |
| `/api/v1/auth` | auth | 认证(登录/注册/me) |
| `/api/v1/knowledge` | knowledge | 知识库分类/条目/导入 |
| `/api/v1/knowledge/internal` | knowledge-internal | 知识库内部检索 |
| `/api/v1/reports` | reports / reports-batch | 体检报告解析、批量上传 |
| `/api/v1/interpretations` | interpretations | AI 解读、分诊规则、高危列表 |
| `/api/v1/statistics` | statistics | 统计看板、分组统计、导出 |
| `/api/v1/dispatch` | dispatch | 资源监控、队列、分诊配置 |
| `/api/v1/chat` | chat | 聊天会话与消息(SSE) |
| `/api/v1/profile` | user-profile | 个人健康画像 |
| `/api/v1/tenants` | tenant | 平台级租户管理 |

### 1.3 认证

- 登录/注册接口返回 `access_token`(JWT),payload 含 `user_id`、`role`、`hospital_id`。
- 需鉴权的接口通过 `Authorization: Bearer <token>` 传递,由 `get_current_user` 解析。
- 角色取值:`user` / `doctor` / `admin`。
- 部分接口需 `admin` 角色(`require_role("admin")` 或 `batch_router._db`)。

### 1.4 医院上下文(hospital context)

两类依赖决定 `hospital_id` 来源:

- **显式用户依赖**:`get_current_user` 解析 JWT 后把 `hospital_id` 写入 ContextVar(`set_current_hospital_id`),同时作为 `CurrentUser.hospital_id` 返回。
- **上下文读取**:knowledge / statistics / dispatch 的 `_get_hospital_id` 直接读 ContextVar;若未设置则返回 `400 "Hospital context required"`。

> 注:knowledge / statistics / dispatch 路由本身**未显式挂 `get_current_user`**,其 `hospital_id` 依赖请求链路中先行解析的 JWT 上下文。跨服务直接调用时请确保已携带合法 Bearer 头或使用内部检索接口(见 §5)。

### 1.5 统一错误格式

业务异常继承 `HTTPException`,响应体为 FastAPI 标准:

```json
{ "detail": "<错误描述>" }
```

业务异常类与状态码:

| 异常 | HTTP 状态码 | code |
|------|------------|------|
| `ValidationException` | 400 | `VALIDATION_ERROR` |
| `UnauthorizedException` | 401 | `UNAUTHORIZED` |
| `ForbiddenException` | 403 | `FORBIDDEN` |
| `NotFoundException` | 404 | `NOT_FOUND` |
| 未捕获异常 | 500 | `INTERNAL_ERROR` |

---

## 2. 认证 `auth`

前缀:`/api/v1/auth`

### 2.1 登录
`POST /login`

请求体:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| username | string | 是 | 用户名 |
| password | string | 是 | 密码 |

响应 `TokenResponse`:

| 字段 | 类型 | 说明 |
|------|------|------|
| access_token | string | JWT |
| token_type | string | 固定 `"bearer"` |
| user_id | int | 用户 ID |
| role | string | 角色 |
| hospital_id | string\|null | 医院 ID |

### 2.2 注册
`POST /register`

请求体:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| username | string | 是 | 用户名 |
| password | string | 是 | 密码 |
| role | string | 是 | `user`/`doctor`/`admin` |
| hospital_id | string\|null | 否 | 医院 ID |

响应:`TokenResponse`(同 2.1)。

### 2.3 当前用户
`GET /me` —— 需鉴权

响应:`TokenResponse`(其中 `access_token` 为空字符串,其余字段回填)。

---

## 3. 健康检查 `health`

### 3.1 健康检查
`GET /api/v1/health`

响应:`{ "status": "ok" }`

---

## 4. 知识库 `knowledge`

前缀:`/api/v1/knowledge`(依赖医院上下文)

### 4.1 分类

#### 列表
`GET /categories` → `list[CategoryResponse]`

#### 创建
`POST /categories` —— 请求体 `CategoryCreate`:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | 是 | 1–100 字符 |
| parent_id | int\|null | 否 | 父分类 |
| sort_order | int | 否 | 排序(默认 0) |

#### 更新
`PUT /categories/{category_id}` —— 请求体 `CategoryUpdate`(字段均可选:`name`、`parent_id`、`sort_order`)

#### 删除
`DELETE /categories/{category_id}` → `{ "status": "deleted" }`

`CategoryResponse` 字段:`id`、`name`、`parent_id`、`sort_order`、`created_at`、`updated_at`。

### 4.2 条目

#### 列表
`GET /entries`

Query 参数:

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| category_id | int\|null | — | 按分类过滤 |
| page | int | 1 | ≥1 |
| page_size | int | 20 | 1–100 |

响应 `EntryListResponse`:`{ items, total, page, page_size }`。

#### 创建
`POST /entries` —— 请求体 `EntryCreate`:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | string | 是 | 1–200 字符 |
| content | string | 是 | 正文 |
| category_id | int\|null | 否 | 分类 |

#### 详情
`GET /entries/{entry_id}` → `EntryResponse`

#### 更新
`PUT /entries/{entry_id}` —— 请求体 `EntryUpdate`(`category_id`、`title`、`content` 均可选)

#### 删除
`DELETE /entries/{entry_id}` → `{ "status": "deleted" }`

`EntryResponse` 字段:`id`、`category_id`、`title`、`content`、`source_type`、`source_file`、`status`、`created_at`、`updated_at`。

### 4.3 导入文档
`POST /import` —— `multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | file | 是 | `.pdf/.docx/.doc/.xlsx/.xls/.txt/.md` |
| category_id | int | 否 | 目标分类 |

响应:`{ "imported": <条数>, "filename": "<原文件名>" }`

### 4.4 重建索引
`POST /reindex/{category_id}` → `{ "status": "reindexed", "category_id": <id> }`

---

## 5. 知识库内部检索 `knowledge-internal`

前缀:`/api/v1/knowledge/internal`

### 5.1 向量检索
`POST /search` —— 请求体 `SearchRequest`:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| hospital_id | string | 是 | 医院 ID(显式传入,不依赖 JWT) |
| query | string | 是 | 查询文本 |
| top_k | int | 否 | 1–20,默认 5 |
| category_ids | list[int]\|null | 否 | 分类过滤 |

响应 `SearchResponse`:`{ results: [SearchResult] }`。

---

## 6. 体检报告 `reports`

前缀:`/api/v1/reports`(需鉴权)

### 6.1 上传报告
`POST /upload` —— `multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | file | 是 | `.pdf/.docx/.doc/.jpg/.jpeg/.png`,≤20MB |

响应 `TaskStatusResponse`:

| 字段 | 类型 | 说明 |
|------|------|------|
| task_id | int | 解析任务 ID |
| status | string | 任务状态 |
| error_message | string\|null | 错误信息 |
| created_at | datetime | 创建时间 |
| completed_at | datetime\|null | 完成时间 |

### 6.2 任务状态
`GET /tasks/{task_id}` → `TaskStatusResponse`

### 6.3 报告列表
`GET ""`(即 `/api/v1/reports`)

Query 参数:`page`(≥1,默认 1)、`page_size`(1–100,默认 20)。

> 角色为 `user` 时仅返回本人报告;`doctor`/`admin` 返回全院报告。

响应:`{ items, total, page, page_size }`。

### 6.4 报告详情
`GET /{report_id}` → `ReportDetailResponse`:

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 报告 ID |
| task_id | int\|null | 关联任务 |
| name | string\|null | 姓名 |
| gender | string\|null | 性别 |
| age | int\|null | 年龄 |
| report_date | date\|null | 报告日期 |
| check_type | string\|null | 检查类型 |
| unit_name | string\|null | 单位名称 |
| indicators | list | 指标列表(见下) |
| created_at | datetime | 创建时间 |

指标 `ReportIndicatorSchema` 字段:`item_name`、`item_name_standard`、`item_code`、`result_value`、`unit`、`ref_range_low`、`ref_range_high`、`category`。

### 6.5 删除报告
`DELETE /{report_id}` → `{ "status": "deleted" }`

> 级联删除关联的解读、指标、会话与消息。

---

## 7. 批量上传 `reports-batch`

前缀:`/api/v1/reports`(需鉴权,**仅 `admin`**,否则 403)

三步流程:创建批次 → 分片上传 → complete 触发后台解压解析 → 轮询进度。

### 7.1 创建批次
`POST /batches` —— `multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| filename | string | 是 | 压缩包名 |

响应:`{ "batch_id": "<32位hex>" }`

### 7.2 上传分片
`POST /batches/{batch_id}/chunk` —— `multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| index | int | 是 | 0-based 序号 |
| total | int | 是 | 总片数 |
| data | file | 是 | 本片二进制 |

响应:`{ "received": index, "total": total }`

### 7.3 完成上传
`POST /batches/{batch_id}/complete` —— JSON body:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| expected_total | int | 是 | 期望总片数 |
| expected_size | int | 是 | 期望总字节数 |
| expected_crc32 | string\|null | 否 | CRC32 校验 |

响应:`{ "batch_id": ..., "status": "extracting" }`

失败返回 400,`detail` 为 `archive_too_large` / `crc_mismatch` / `chunks_incomplete` 之一。

### 7.4 批次列表
`GET /batches`

Query 参数:`page`(≥1)、`page_size`(1–100)、`status`(可选过滤)。

响应 `items` 元素:`id`、`filename`、`status`、`total`、`parsed_ok`、`interp_ok`、`failed`、`created_at`。

### 7.5 批次详情(进度)
`GET /batches/{batch_id}` → `get_progress` 结果:

```json
{
  "batch": { "id", "filename", "status", "total", "parsed_ok", "interp_ok",
             "failed", "error_message", "created_at", "completed_at" },
  "failing_files": [ { "id", "file_path", "failed_stage", "error_message" } ]
}
```

`failed_stage` 取值:`parsing` / `interpretation` / `oversize` / `dispatch_unmatched`。

### 7.6 死信查看
`GET /batches/{batch_id}/dead` → `{ "dead": [...] }`

### 7.7 失败重试
`POST /batches/{batch_id}/retry` —— JSON body:`{ "file_ids": [..] }`(可选)

响应:`{ "requeued": <重投数>, "skipped_unretryable": <跳过数> }`

> `oversize` / `dispatch_unmatched` 两类失败不可重试,计入 `skipped_unretryable`。

### 7.8 取消批次
`POST /batches/{batch_id}/cancel` → `{ "cancelled": true }`

> `completed` / `partial_failed` 状态不可取消(400)。

---

## 8. 解读 `interpretations`

前缀:`/api/v1/interpretations`(需鉴权)

### 8.1 高危列表
`GET /high-risk/list` → `HighRiskResponse`:`{ items, total }`

### 8.2 分诊规则

#### 列表
`GET /rules/all` → `list[TriageRuleResponse]`

#### 创建
`POST /rules` —— 请求体 `TriageRuleCreate`:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| rule_name | string | 是 | 规则名 |
| rule_type | string | 是 | 规则类型 |
| indicator_code | string\|null | 否 | 指标编码 |
| conditions | dict | 是 | 条件 |
| color_level | string | 是 | 颜色分级 |
| priority | int | 否 | 优先级(默认 0) |

#### 更新
`PUT /rules/{rule_id}` —— 请求体 `TriageRuleUpdate`(全字段可选,含 `is_active`)

#### 删除
`DELETE /rules/{rule_id}` → `{ "status": "deleted" }`

`TriageRuleResponse` 字段:`id`、`rule_name`、`rule_type`、`indicator_code`、`conditions`、`color_level`、`priority`、`is_active`、`created_at`、`updated_at`。

### 8.3 报告解读详情
`GET /{report_id}` → `InterpretationResponse`:

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 解读 ID |
| report_id | int | 报告 ID |
| overall_level | string\|null | 总体分级 |
| red_count / yellow_count / green_count | int | 红/黄/绿计数 |
| status | string | 状态 |
| summaries | object | 摘要(见下) |
| references | list | 引用文献 |
| quality_note | string\|null | 质量说明 |
| indicators | list | 指标判定 |
| created_at / completed_at | datetime | 时间 |

`summaries`(`InterpretationReportSchema`)字段:`overall_summary`、`abnormal_focus`、`trend_note`、`suggestions`、`risk_alert`。

`IndicatorJudgmentSchema` 字段:`indicator_id`、`item_name`、`result_value`、`unit`、`ref_range_low`、`ref_range_high`、`deviation`、`color_level`。

### 8.4 报告指标判定
`GET /{report_id}/indicators` → 指标判定列表。

---

## 9. 统计 `statistics`

前缀:`/api/v1/statistics`(依赖医院上下文;`/group/*` 需 `admin`)

### 9.1 看板概览
`GET /dashboard`

Query 参数:`start_date`(必)、`end_date`(必,`YYYY-MM-DD`)。

### 9.2 健康画像
`GET /health-profile`

Query 参数:`start_date`(必)、`end_date`(必)、`unit_name`(可选)。

### 9.3 交叉对比
`GET /cross-compare`

Query 参数:`start_date`(必)、`end_date`(必)、`x_dimension`(默认 `unit`)、`unit_name`(可选)。

### 9.4 趋势分析
`GET /trend`

Query 参数:`indicator`(必)、`years`(1–10,默认 5)。

### 9.5 导出
`POST /export` —— 请求体 `ExportRequest`:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| hospital_id | string | 是 | 医院 ID |
| template_id | int\|null | 否 | 模板 |
| export_type | string | 否 | 默认 `pdf` |
| start_date / end_date | date | 是 | 区间 |

响应:`{ "status": "queued", "message": "Export task submitted" }`

### 9.6 分组概览(admin)
`GET /group/overview`

Query 参数:`group_by`(必,`hospital`/`batch`/`age_group`/`gender`/`time_month`),及以下过滤项。

### 9.7 分组高危(admin)
`GET /group/high-risk`

Query 参数:

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| sort | string | `red_count` | `red_count`/`age`/`report_date` |
| page | int | 1 | ≥1 |
| page_size | int | 20 | 1–200 |
| format | string | `json` | `json`/`csv` |

`format=csv` 时返回 `text/csv` 流式下载。

分组过滤参数(两个接口共用):

| 参数 | 类型 | 说明 |
|------|------|------|
| hospital_ids | string | 逗号分隔 |
| batch_ids | string | 逗号分隔 |
| date_from / date_to | date | 区间 |
| gender | string | `M`/`F`/`男`/`女` |
| age_groups | string | 逗号分隔 |
| topn | int | 默认 10,1–100 |

---

## 10. 分诊/资源 `dispatch`

前缀:`/api/v1/dispatch`(依赖医院上下文)

### 10.1 当前资源指标
`GET /metrics/current` → `ResourceMetricResponse`:

| 字段 | 类型 |
|------|------|
| cpu_percent | float |
| memory_percent | float |
| gpu_percent | float\|null |
| gpu_memory_percent | float\|null |
| queue_depth_parsing | int |
| queue_depth_interpretation | int |
| active_workers | int |

### 10.2 队列状态
`GET /queues` → `[{ queue_name, depth, consumer_count }]`

### 10.3 获取配置
`GET /config`

### 10.4 更新配置
`PUT /config` —— 请求体 `DispatchConfigUpdate`(全可选):

`max_parsing_workers`、`max_interpretation_workers`、`queue_alert_threshold`、`task_retry_max`、`task_timeout_seconds`。

---

## 11. 聊天 `chat`

前缀:`/api/v1/chat`(需鉴权)

### 11.1 会话列表
`GET /sessions` → `list[SessionResponse]`

### 11.2 创建会话
`POST /sessions` —— 请求体 `CreateSessionRequest`:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| report_id | int\|null | 否 | 关联报告 |

### 11.3 会话详情
`GET /sessions/{session_id}` → `SessionResponse`

### 11.4 更新会话
`PATCH /sessions/{session_id}` —— 请求体 `CreateSessionRequest`(更新关联报告)

响应:`{ "status": "ok", "report_id": <id> }`

### 11.5 删除会话
`DELETE /sessions/{session_id}` → `{ "status": "deleted" }`

### 11.6 消息列表
`GET /sessions/{session_id}/messages` → `list[MessageResponse]`

`MessageResponse` 字段:`id`、`session_id`、`role`、`content`、`knowledge_refs`、`created_at`。

### 11.7 发送消息(SSE)
`POST /sessions/{session_id}/messages` —— 请求体 `SendMessageRequest`:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| content | string | 是 | 1–4000 字符 |

响应:`text/event-stream`,事件格式:

```
event: <event_type>
data: <json>
```

事件类型:

| event | data | 说明 |
|-------|------|------|
| `token` | `{ content }` | 流式文本增量 |
| `tool_status` | `{ tool, status }` | 工具调用开始/结束(`start`/`end`) |
| `structured` | `{ ... }` | 结构化数据 |
| `done` | `{ message_id }` | 完成 |
| `error` | `{ message }` | 错误 |

---

## 12. 用户画像 `profile`

前缀:`/api/v1/profile`(需鉴权)

### 12.1 健康概览
`GET /overview`

### 12.2 报告对比
`GET /compare`

Query 参数:`report_id`(必)、`baseline_id`(可选,基线报告)。

### 12.3 AI 总结
`GET /ai-summary`

Query 参数:`report_id`(必)、`baseline_id`(必)。

响应:`{ "ai_summary": <文本>, "cached": <bool> }`

---

## 13. 租户 `tenant`

前缀:`/api/v1/tenants`(平台级)

### 13.1 租户列表
`GET ""` —— 需 `admin` 角色;Query 参数 `active_only`(默认 `true`)。

响应 `TenantListResponse`:`{ items: [{ hospital_id, hospital_name, is_active }], total }`。

### 13.2 创建租户
`POST ""` —— 需请求头 `X-Admin-Token: <ADMIN_TOKEN>`(与后端 `settings.ADMIN_TOKEN` 匹配)。

请求体 `TenantCreateRequest`:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| hospital_id | string | 是 | 2–16 位字母数字,不含下划线 |
| hospital_name | string | 是 | 1–100 字符 |

响应 `TenantCreateResponse`:`{ created, hospital_id, db_name, hospital_name, is_active }`。

> 新建租户会创建独立数据库并执行 `start.sh` 中的完整 DDL 表清单(详见 `AGENTS.md`)。
