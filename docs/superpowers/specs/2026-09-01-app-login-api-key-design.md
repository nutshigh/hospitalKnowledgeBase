# 外部 App 免密登录接口(app-login)设计

**日期**:2026-09-01
**状态**:Draft(已与用户对齐各节,待 review)
**分支**:`feat/login-change-and-interface-expose`

**前置**:
- 批量上传「姓名 + 身份证后六位」双锚定:`docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md`(含 §15,已交付、未 commit)
- 外部接口 resolver:`backend/app/core/hospital_resolver.py` + `EXTERNAL_RESOLVER_URL` 配置
- 工程约束:`AGENTS.md`

---

## 0. 目标与边界

### 目标
把**报告上传 / chat 等用户功能**开放给外部 App(如医院 HIS)调用。外部 App 不掌握每个用户的 username/password,而是:

- 用**应用级密钥**(`APP_API_KEY`,单个全局 key,`.env` 配置)自证身份
- 每次为某用户取 token 时,传 `name` + `id_card_suffix`,后端调 `EXTERNAL_RESOLVER_URL` 由后六位解析出 `hospital_id`
- 签发**与普通登录完全相同的 JWT**,此后对 `/api/v1/reports/*`、`/api/v1/chat/*` 的调用全部复用现有 Bearer 认证,**现有 router 零改动**

### 范围内
- 新增 `POST /api/v1/auth/app-login` 接口
- 新配置 `APP_API_KEY`、`APP_LOGIN_TOKEN_EXPIRE_MINUTES`
- 自动注册:三元组不存在时合成 username 建 `platform_user`(role='user', 无密码)
- 错误处理(401/400/503)
- 测试 + `.env.example` + `AGENTS.md` 文档

### 范围外(YAGNI)
- 多 app / 多 key 管理(单全局 key,后续需要再演进)
- app 密钥吊销/轮换机制(改 `.env` 重启即可)
- 接口级限流/审计
- doctor/admin 端接口暴露(仅 user 级报告 + chat)
- `report_task` 加 `name` 列(沿用现状,name 落 `report_info`)

---

## 1. 关键决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| D1 | 接入模式 | **B2:免密登录换 token** | 外部 App 只需改登录模块,其余接口调用零改动 |
| D2 | app 密钥存储 | **`.env` 单个全局 key** | 单外部系统接入,最简 |
| D3 | hospital_id 来源 | **后端用 resolver 从后六位解析** | App 少传参数;复用现有 `EXTERNAL_RESOLVER_URL` |
| D4 | 未注册用户 | **自动注册** | 一次调用即打通;唯一性由 `uq_platform_user_anchor` 兜底 |
| D5 | token 有效期 | **长有效期,单独配置** `APP_LOGIN_TOKEN_EXPIRE_MINUTES=10080`(7 天) | 减少 App 刷新频率 |
| D6 | 接口范围 | **报告全部 + chat 全部**(role='user' 可覆盖) | 用户确认 |
| D7 | 自动注册密码 | **随机串 hash,不可密码登录** | 仅 App 驱动,不暴露密码登录面 |
| D8 | 信任模型 | **app_key 持有人可代任意 (name, 后六位) 签发 token** | 需 TLS + key 保密,仅给可信 HIS |

---

## 2. 接口契约

```
POST /api/v1/auth/app-login
```

请求体:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `app_key` | string | 是 | 与 `APP_API_KEY` 常量时间比较 |
| `name` | string | 是 | 姓名(与报告文件名姓名段一致,`platform_user.name`) |
| `id_card_suffix` | string | 是 | 身份证后六位,正则 `^[0-9]{5}[0-9X]$` |

成功响应(200):与 `/api/v1/auth/login` 相同的 `TokenResponse` 结构:
`access_token` / `token_type` / `user_id` / `role`('user') / `hospital_id` / `id_card_suffix` / `name`。

错误响应:

| 场景 | 状态码 | detail |
|------|--------|--------|
| `APP_API_KEY` 未配置或 `app_key` 不匹配 | 401 | Invalid app key |
| `name` 为空 | 400 | name required |
| `id_card_suffix` 为空或格式非法 | 400 | id_card_suffix required (5 digits + digit or X) |
| resolver 返回 `None`(无匹配) | 401 | 无法匹配用户医院 |
| resolver 抛 `ResolverUnavailableError` | 503 | resolver 不可用 |

---

## 3. 流程

```
app-login(app_key, name, id_card_suffix)
  ├─ 若 not APP_API_KEY or not secrets.compare_digest(app_key, APP_API_KEY) → 401
  ├─ 校验 name / id_card_suffix → 400
  ├─ hospital_id = resolve_hospital(id_card_suffix)      # 复用 hospital_resolver
  │    ├─ None → 401
  │    └─ ResolverUnavailableError → 503
  ├─ 在 template DB 查 platform_user (hospital_id, name, id_card_suffix)
  │    ├─ 命中 → 用之
  │    └─ 未命中 → 自动注册(见 §4)
  ├─ token = create_access_token(payload, expires_delta=APP_LOGIN_TOKEN_EXPIRE_MINUTES)
  └─ 返回 TokenResponse
```

签发 token 的 payload 与普通登录一致:
`user_id`(platform_user.id)/ `role`='user' / `hospital_id` / `id_card_suffix` / `name`。
下游 `get_current_user` / `user_identity`(`dependencies.py`)与 chat / report router **零改动**直接复用。

---

## 4. 自动注册

当 `(hospital_id, name, id_card_suffix)` 三元组在 `platform_user` 不存在时:

- **username 合成**: `app_<hospital_id>_<name>_<id_card_suffix>`(如 `app_H001_张三_12345X`)
  - 三元组在单医院内唯一(`uq_platform_user_anchor` 唯一索引)→ 该 username 全局唯一、确定性可重建 → 二次调用命中已有行,天然幂等
  - `username` 列 `VARCHAR(50)`,`NOT NULL UNIQUE`
- **password_hash**: `hash_password(secrets.token_urlsafe(32))`(随机,不可密码登录)
- **role**: `user`;`hospital_id` / `name` / `id_card_suffix` 落库;`is_active` 默认 1
- **并发竞态**: 两请求同时发现不存在 → 各自 INSERT → `uq_platform_user_anchor` 唯一索引兜底,捕获 `IntegrityError` 后回查已有行继续签发(幂等)

---

## 5. 安全说明

- 该接口是**信任模型**: 持有 `APP_API_KEY` 的外部系统可代任意 `(name, 后六位)` 签发 user 级 token,等于可访问任意已注册/未注册用户的报告与 chat。
- 必须依赖:TLS 传输、key 保密、仅授予可信 HIS 系统。
- 自动注册产生的用户**无密码**,不参与密码登录,无密码爆破面。
- `app_key` 用 `secrets.compare_digest` 常量时间比较,防时序侧信道。
- 建议后续(范围外)加:key 轮换、调用审计、频控。

---

## 6. 测试

新增 `backend/tests/test_auth_app_login.py`(沿用现有 fake-DB / monkeypatch 风格):

1. 正确 key + 已注册用户 → 200,payload 含正确 name/suffix/hospital_id
2. 正确 key + 未注册用户 → 自动建行并签发;再次调用返回同一用户(幂等)
3. 错误 key → 401
4. `APP_API_KEY` 未配置 → 401
5. suffix 非法 / name 为空 → 400
6. resolver 返回 None → 401
7. resolver 抛 `ResolverUnavailableError` → 503
8. token 有效期使用 `APP_LOGIN_TOKEN_EXPIRE_MINUTES`(非默认)

测试基线:`cd backend && .venv/bin/pytest tests/ -q`(现状 276 passed / 2 pre-existing failed)

---

## 7. 改动文件清单(预估)

| 文件 | 改动 |
|------|------|
| `backend/app/config.py` | 新增 `APP_API_KEY: str = ""`、`APP_LOGIN_TOKEN_EXPIRE_MINUTES: int = 10080` |
| `backend/app/api/auth.py` | 新增 `AppLoginRequest` + `app-login` 端点 + 自动注册逻辑 |
| `backend/app/core/hospital_resolver.py` | 复用,不改 |
| `backend/.env.example` | 新增两行注释占位 |
| `backend/tests/test_auth_app_login.py` | 新增测试 |
| `AGENTS.md` | 新增 app-login 信任模型 + 配置说明小节 |
| `docs/superpowers/specs/2026-09-01-app-login-api-key-design.md` | 本文件 |

无新增 DDL / 无迁移;自动注册只写 `platform_user`(已含新列,前提是存量库先跑 `003_user_id_suffix.sql`)。

---

## 8. 落地顺序

1. 先 commit 现有 `feat/login-change-and-interface-expose` 分支上的双锚定改动(39 M + 6 ??,用户自行 commit)
2. 本 spec review 通过 → writing-plans → 按 plan 实现 + 测试
3. 存量库迁移 `003_user_id_suffix.sql` **先于**后端部署(本功能依赖新列)
4. `.env` 配置 `APP_API_KEY` + `EXTERNAL_RESOLVER_URL`
5. 外部 App 改登录模块为调 `/api/v1/auth/app-login`,其余报告/chat 调用原样
