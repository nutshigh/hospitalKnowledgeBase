# 接入 baUser 开放接口(searchUser)解析用户医院 设计

**日期**:2026-09-01
**状态**:Draft(已与用户对齐,待 review)
**分支**:`feat/login-change-and-interface-expose`

**前置**:
- 批量上传「姓名 + 身份证后六位」双锚定:`docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md`(已交付)
- app-login 免密登录:`docs/superpowers/specs/2026-09-01-app-login-api-key-design.md`(已交付)
- 外部接口文档:`docs/baUser-open-api.md`(本次接入的对象)
- 工程约束:`AGENTS.md`

---

## 0. 目标与边界

### 目标
把 `hospital_resolver` 从「按后六位 POST 占位契约」改为**对接 baUser 开放接口 `searchUser`**,使批量上传与 app-login 两个场景能真实解析出用户所在医院。

### 关键约定(用户确认)
- **orgId 即 hospital_id**:外部返回的 `orgId` 直接作为本地 `hospital_tenant.hospital_id` 使用,无需映射。
- **姓名全匹配**:`realName` 传入真实姓名,外部按全名匹配,不会匹配到其他人。
- **本地租户不存在 → 短路**:`hospital_tenant` 无该 hospital_id 时,批量上传记 `hospital_not_found`(不可重试),不自动建租户。

### 范围内
- `hospital_resolver` 契约改 `(name, id_suffix)` → GET `searchUser` → 解析 `{code,msg,data}` 信封 → 精确过滤 → 返回 `str(orgId)` / None / 抛 `ResolverUnavailableError`
- 两个调用方适配:`extract_worker`(传 name + 复合缓存 key)、`app_login`(传 name)
- 测试更新 + `.env.example` 注释
- `AGENTS.md` 中 resolver 契约描述同步

### 范围外(YAGNI)
- 自动建租户(用户确认:短路)
- orgId ↔ hospital_id 映射配置(用户确认:orgId 即 hospital_id)
- 接口一 `page` 对接(无后六位过滤,解析场景用不到)
- 用户同步/档案拉取(本设计只做"解析医院"一件事)

---

## 1. 关键决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| D1 | 解析接口 | **`searchUser?realName=&idCardLast6=`** | 两个场景都有 name+suffix;接口二正好支持双条件 |
| D2 | orgId 语义 | **orgId 即 hospital_id,直接使用** | 用户确认 |
| D3 | 姓名匹配 | **全匹配,且客户端精确过滤** | 用户确认姓名全匹配;仍做 `realName==name AND idCardLast6==suffix` 防御 |
| D4 | 本地租户不存在 | **短路 `hospital_not_found`,不自动建租户** | 用户确认,避免误建 |
| D5 | 业务失败语义 | **body `code!=200` 视为下游错误 → `ResolverUnavailableError`(可重试)** | HTTP 状态可能恒 200,失败在 body code |
| D6 | 歧义处理 | **≥2 条且 orgId 不同 → None(warning)** | 防止串号落库 |
| D7 | 缓存 key | **`(name, id_suffix)` 复合键** | 同一批不同姓名可能同后缀,防串号 |
| D8 | orgId 类型 | **`str(orgId)` 转字符串** | JSON 中为数字 Long,hospital_id 是 VARCHAR |
| D9 | URL 语义 | **`EXTERNAL_RESOLVER_URL` 直接指向 searchUser 完整 URL** | 复用现有配置项,只改注释 |

---

## 2. 接口契约(消费方视角)

```
resolve_hospital(name: str, id_suffix: str) -> Optional[str]
```

- 发起:`GET {EXTERNAL_RESOLVER_URL}?realName={name}&idCardLast6={id_suffix}`
  - `EXTERNAL_RESOLVER_URL` = `http://localhost:82/snowyApi/biz/baUserOpen/searchUser`(上线前运维换真实网关地址)
  - `EXTERNAL_RESOLVER_TIMEOUT` 复用现有配置
- 返回:
  - `hospital_id`(str,来自 `data` 中唯一精确匹配项的 `orgId`)匹配
  - `None` 明确无匹配(未配置 / 无精确命中 / 歧义 / HTTP 4xx)
  - 抛 `ResolverUnavailableError`(超时 / 传输错 / HTTP 5xx / body `code!=200`)

### 匹配与解析规则(`_parse_response`)

对响应体(baUser 统一信封 `{code, msg, data}`):

1. `data` 为数组;对每项取 `realName` / `idCardLast6` / `orgId`
2. 过滤条件:**`realName == name` 且 `idCardLast6 == id_suffix`**(全等,防御性精确匹配)
3. 命中 0 条 → None
4. 命中 1 条 → `str(orgId)`
5. 命中 ≥2 条:若全部 orgId 相同 → `str(orgId)`;否则视为歧义 → None 并记 warning 日志
6. `data` 为 null / 空数组 → None

### 错误分类

| 场景 | 结果 |
|------|------|
| `EXTERNAL_RESOLVER_URL` 未配置 | None |
| HTTP 4xx | None(log warning,同现状) |
| HTTP 5xx / 网络超时 / 传输错 | `ResolverUnavailableError` |
| body `code != 200` | `ResolverUnavailableError`(下游系统错误,可重试) |
| body 解析失败(非 JSON) | `ResolverUnavailableError` |

---

## 3. 调用方适配

### 3.1 extract_worker

`_resolve_hospital_id(batch_id, id_suffix)` → `_resolve_hospital_id(batch_id, name, id_suffix)`:

- 批内缓存 `_batch_resolver_cache: dict[batch_id, dict[(name, id_suffix) -> hospital_id|None]]`,key 改复合 `(name, id_suffix)`
- 两个调用点(`zip`/`tar` 分支,`_extract_and_enqueue` 内)把文件名解析出的 `name` 一并传入
- 本地 `_hospital_registered` 校验不变;不通过 → `hospital_not_found`

### 3.2 app_login

`auth.py` 的 `app_login`:
- `resolve_hospital(req.id_card_suffix)` → `resolve_hospital(name, req.id_card_suffix)`
- `name` 使用已 strip 的局部变量(当前实现已做 `name = req.name.strip()`)
- 其余流程(查三元组 / 自动注册 / 签发 token)不变

---

## 4. 测试

更新 `backend/tests/core/test_hospital_resolver.py`(沿用现有 fake-httpx stub 风格):

1. 精确命中 1 条(orgId=1000002)→ 返回 `"1000002"`(断言 str 转换)
2. 空数组 → None
3. 歧义(2 条不同 orgId)→ None
4. body `code=500` → `ResolverUnavailableError`
5. HTTP 5xx → `ResolverUnavailableError`
6. URL 未配置 → None

适配调用方测试:
- `tests/test_auth_app_login.py`:`ctx` fixture 中 `resolve_hospital` stub 改签名为 `lambda name, suf: "H001"`
- `tests/test_extract_worker.py`:相关断言改为复合缓存 key(如 `("张三","12345X")`)

全量回归:`cd backend && .venv/bin/pytest tests/ -q` → 基线 290 passed / 2 pre-existing failed。

---

## 5. 落地顺序

1. 改 `backend/app/core/hospital_resolver.py`(契约 + 信封解析 + 精确过滤)
2. 改 `backend/app/modules/report/extract_worker.py`(传 name + 复合缓存 key)
3. 改 `backend/app/api/auth.py`(app_login 传 name)
4. 更新测试 + 全量回归
5. commit + 更新 `AGENTS.md`(resolver 契约描述 + `EXTERNAL_RESOLVER_URL` 指向 searchUser)

---

## 6. 上线前验证项(真机)

- `EXTERNAL_RESOLVER_URL` 指向真实网关地址(`https://szbf.yiqikang.cn/snowyApi/biz/baUserOpen/searchUser`)
- 本地 `hospital_tenant` 已按 orgId 值建好租户(`hospital_id == str(orgId)`)
- 真机验证:姓名 + 后六位 → orgId 一致;后六位末位 `X` 大小写行为;姓名与文件名姓名段逐字一致

---

## 7. 改动文件清单(预估)

| 文件 | 改动 |
|------|------|
| `backend/app/core/hospital_resolver.py` | 签名 `(name, id_suffix)`、GET query、信封解析、精确过滤、错误分类 |
| `backend/app/modules/report/extract_worker.py` | `_resolve_hospital_id` 传 name、复合缓存 key、两调用点 |
| `backend/app/api/auth.py` | `app_login` 调 resolver 传 name |
| `backend/tests/core/test_hospital_resolver.py` | 新增 6 用例 + 适配签名 |
| `backend/tests/test_auth_app_login.py` | `ctx` fixture stub 签名 |
| `backend/tests/test_extract_worker.py` | 缓存 key 断言 |
| `backend/.env.example` | `EXTERNAL_RESOLVER_URL` 注释更新 |
| `AGENTS.md` | resolver 契约描述同步 |

无新增 DDL / 无迁移。
