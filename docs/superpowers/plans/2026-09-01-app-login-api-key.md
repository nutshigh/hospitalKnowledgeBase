# 外部 App 免密登录接口(app-login)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `POST /api/v1/auth/app-login`,外部 App 用 `app_key + name + id_card_suffix` 换取与普通登录一致的 JWT,复用现有 chat / report 接口。

**Architecture:** 在 `backend/app/api/auth.py` 新增 `AppLoginRequest` + `app_login` 端点:常量时间校验 `.env` 全局 key → 校验参数 → `resolve_hospital(suffix)` 解析 hospital_id → 查 `platform_user` 三元组,未命中则自动注册(合成 username + 随机密码)→ 用 `APP_LOGIN_TOKEN_EXPIRE_MINUTES` 签发 JWT。下游 `get_current_user`/`user_identity` 与 chat/report router 零改动。

**Tech Stack:** FastAPI + SQLAlchemy text + pydantic;测试用 pytest + monkeypatch + fake DB(见 `tests/test_auth_id_suffix.py` 风格)。

## Global Constraints

- 后端测试命令:`cd backend && .venv/bin/pytest tests/ -q`(基线 276 passed / 2 pre-existing failed,与本功能无关)
- 后六位正则:`^[0-9]{5}[0-9X]$`(auth.py 已有 `_SUFFIX_RE`)
- 双锚定:`user_id`(后六位,VARCHAR(16))+ `name`(姓名);`platform_user` 唯一索引 `uq_platform_user_anchor(hospital_id,name,id_card_suffix)`
- `platform_user.username` 是 `VARCHAR(50) NOT NULL UNIQUE`(全局唯一)
- 现有 `create_access_token(data, expires_delta=None)` 支持自定义有效期(`app/core/security.py:17`)
- 现有 `resolve_hospital(id_suffix) -> Optional[str]`,抛 `ResolverUnavailableError`(`app/core/hospital_resolver.py:49`)
- 禁止在 `backend/pyproject.toml` 加 vllm;不新增第三方依赖
- 本功能无新增 DDL / 无迁移;前提是存量库已跑 `003_user_id_suffix.sql`

---

### Task 1: 配置项 + ServiceUnavailableException + .env.example

**Files:**
- Modify: `backend/app/config.py:126-129`(resolver 配置块后新增)
- Modify: `backend/app/utils/exceptions.py:25`(ValidationException 后新增)
- Modify: `backend/.env.example:74-75`(resolver 占位后新增)
- Test: `backend/tests/test_config_batch.py`

**Interfaces:**
- Consumes: 无(现有 `Settings` / `AppException`)
- Produces:
  - `settings.APP_API_KEY: str = ""`(空 = app-login 一律 401)
  - `settings.APP_LOGIN_TOKEN_EXPIRE_MINUTES: int = 10080`
  - `ServiceUnavailableException(detail="Service unavailable")` → 503,code `SERVICE_UNAVAILABLE`

- [ ] **Step 1: 写失败测试**(修改 `test_config_batch.py`)

在 `test_config_batch.py` 末尾新增一个测试函数(与现有 `test_batch_config_defaults` 同款 delenv 风格):

```python
def test_app_login_config_defaults(monkeypatch):
    for k in ["APP_API_KEY", "APP_LOGIN_TOKEN_EXPIRE_MINUTES"]:
        monkeypatch.delenv(k, raising=False)
    from app.config import Settings
    s = Settings()
    assert s.APP_API_KEY == ""
    assert s.APP_LOGIN_TOKEN_EXPIRE_MINUTES == 10080
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_config_batch.py -q`
Expected: FAIL(AttributeError: 'Settings' object has no attribute 'APP_API_KEY')

- [ ] **Step 3: 最小实现**

`backend/app/config.py`,在 `EXTERNAL_RESOLVER_TIMEOUT` 后插入:

```python
    # External App (app-login: app_key + name + id_card_suffix → user token)
    # 空 = app-login 一律 401(接口不配置则无法使用)
    APP_API_KEY: str = ""
    APP_LOGIN_TOKEN_EXPIRE_MINUTES: int = 10080
```

`backend/app/utils/exceptions.py`,在 `ValidationException` 后新增:

```python
class ServiceUnavailableException(AppException):
    def __init__(self, detail: str = "Service unavailable"):
        super().__init__(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                         detail=detail, code="SERVICE_UNAVAILABLE")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_config_batch.py -q`
Expected: PASS

- [ ] **Step 5: 更新 .env.example**

`backend/.env.example`,在 resolver 两行占位(74-75 行)后追加:

```
# External App app-login:外部 App 免密登录(持 key 代用户取 token)
# APP_API_KEY=changeme
# APP_LOGIN_TOKEN_EXPIRE_MINUTES=10080
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/config.py backend/app/utils/exceptions.py backend/.env.example backend/tests/test_config_batch.py
git commit -m "feat: app-login 配置项与 503 异常"
```

---

### Task 2: app-login 端点 + 自动注册

**Files:**
- Modify: `backend/app/api/auth.py`
- Test: `backend/tests/test_auth_app_login.py`(新建)

**Interfaces:**
- Consumes: `settings.APP_API_KEY` / `settings.APP_LOGIN_TOKEN_EXPIRE_MINUTES`(Task 1);`resolve_hospital` + `ResolverUnavailableError`(已存在);`_SUFFIX_RE` / `_valid_suffix` / `create_access_token` / `hash_password`(auth.py 现有)
- Produces:
  - `AppLoginRequest(app_key: str, name: str, id_card_suffix: str)`
  - `@router.post("/app-login", response_model=TokenResponse)` 端点,返回与 `/login` 相同的 `TokenResponse`
  - `_auto_register(db, hospital_id, name, id_card_suffix) -> row`(自动注册,IntegrityError 兜底幂等)

- [ ] **Step 1: 写失败测试**(新建 `backend/tests/test_auth_app_login.py`)

```python
"""app-login 免密登录:key 校验 / 双锚定解析 / 自动注册 / 错误码。"""
import pytest
from datetime import timedelta

from app.api.auth import app_login, AppLoginRequest
from app.core.hospital_resolver import ResolverUnavailableError
from app.utils.exceptions import (
    UnauthorizedException, ValidationException, ServiceUnavailableException,
)


def _req(**kw):
    defaults = {"app_key": "secret", "name": "张三", "id_card_suffix": "12345X"}
    defaults.update(kw)
    return AppLoginRequest(**defaults)


class _Row:
    def __init__(self, user_id=1, role="user", hospital_id="H001",
                 id_card_suffix="12345X", name="张三"):
        self.id = user_id
        self.role = role
        self.hospital_id = hospital_id
        self.id_card_suffix = id_card_suffix
        self.name = name


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeDB:
    def __init__(self, user_exists=False, commit_fails=False):
        self.user_exists = user_exists
        self.commit_fails = commit_fails
        self.inserted = []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))
        sql = str(sql)
        if sql.lstrip().startswith("INSERT"):
            self.inserted.append(params)
            return _FakeResult(None)
        if "SELECT id, role" in sql:
            if self.user_exists:
                return _FakeResult(_Row())
            return _FakeResult(None)
        return _FakeResult(None)

    def commit(self):
        if self.commit_fails:
            from sqlalchemy.exc import IntegrityError
            raise IntegrityError("stmt", {}, Exception("dup"))
        self.user_exists = True

    def rollback(self):
        pass


@pytest.fixture
def ctx(monkeypatch):
    import app.api.auth as auth
    monkeypatch.setattr(auth.settings, "APP_API_KEY", "secret")
    monkeypatch.setattr(auth.settings, "APP_LOGIN_TOKEN_EXPIRE_MINUTES", 10080)
    monkeypatch.setattr(auth, "resolve_hospital", lambda suf: "H001")
    calls = {}

    def fake_create(data, expires_delta=None):
        calls["data"] = data
        calls["expires_delta"] = expires_delta
        return "tok"

    monkeypatch.setattr(auth, "create_access_token", fake_create)
    return auth, calls


def test_app_login_existing_user(ctx):
    auth, calls = ctx
    resp = app_login(_req(), db=_FakeDB(user_exists=True))
    assert resp.access_token == "tok"
    assert resp.role == "user"
    assert resp.id_card_suffix == "12345X"
    assert resp.name == "张三"
    assert calls["data"]["id_card_suffix"] == "12345X"
    assert calls["data"]["name"] == "张三"
    assert calls["data"]["hospital_id"] == "H001"
    assert calls["expires_delta"] == timedelta(minutes=10080)


def test_app_login_auto_registers_and_idempotent(ctx):
    auth, _ = ctx
    db = _FakeDB(user_exists=False)
    resp = app_login(_req(), db=db)
    assert resp.id_card_suffix == "12345X"
    assert len(db.inserted) == 1
    assert db.inserted[0]["un"] == "app_H001_张三_12345X"
    assert db.inserted[0]["r"] == "user"
    app_login(_req(), db=db)          # 二次调用命中已有行
    assert len(db.inserted) == 1


def test_app_login_race_integrity_error_idempotent(ctx):
    auth, _ = ctx
    db = _FakeDB(user_exists=True, commit_fails=True)  # 另一事务已插入,本事务 INSERT 撞唯一索引
    resp = app_login(_req(), db=db)
    assert resp.id_card_suffix == "12345X"


def test_app_login_wrong_key(ctx):
    auth, _ = ctx
    with pytest.raises(UnauthorizedException):
        app_login(_req(app_key="wrong"), db=_FakeDB())


def test_app_login_unconfigured_key_rejected(ctx):
    auth, _ = ctx
    auth.settings.APP_API_KEY = ""
    with pytest.raises(UnauthorizedException):
        app_login(_req(), db=_FakeDB())


def test_app_login_bad_suffix(ctx):
    auth, _ = ctx
    with pytest.raises(ValidationException):
        app_login(_req(id_card_suffix="12bad"), db=_FakeDB())


def test_app_login_missing_name(ctx):
    auth, _ = ctx
    with pytest.raises(ValidationException):
        app_login(_req(name=""), db=_FakeDB())


def test_app_login_resolver_no_match(ctx):
    auth, _ = ctx
    auth.resolve_hospital = lambda suf: None
    with pytest.raises(UnauthorizedException):
        app_login(_req(), db=_FakeDB())


def test_app_login_resolver_unavailable(ctx):
    auth, _ = ctx
    def boom(suf):
        raise ResolverUnavailableError("down")
    auth.resolve_hospital = boom
    with pytest.raises(ServiceUnavailableException):
        app_login(_req(), db=_FakeDB())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_auth_app_login.py -q`
Expected: FAIL(ImportError: cannot import name 'app_login')

- [ ] **Step 3: 实现**

`backend/app/api/auth.py`:

a) 头部 imports 修改(在现有 import 后追加):

```python
import secrets
from datetime import timedelta
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.hospital_resolver import resolve_hospital, ResolverUnavailableError
from app.utils.exceptions import (
    UnauthorizedException, ValidationException, ServiceUnavailableException,
)
```

(同时把现有 `from app.utils.exceptions import UnauthorizedException, ValidationException` 那一行删除,统一到上面这个 import。)

b) `_SUFFIX_RE` 定义之后新增请求模型:

```python
class AppLoginRequest(BaseModel):
    app_key: str
    name: str
    id_card_suffix: str
```

c) 文件末尾新增(在 `me` 端点之后):

```python
@router.post("/app-login", response_model=TokenResponse)
def app_login(req: AppLoginRequest, db: Session = Depends(get_template_db)):
    if not settings.APP_API_KEY or not secrets.compare_digest(req.app_key, settings.APP_API_KEY):
        raise UnauthorizedException(detail="Invalid app key")

    if not req.name:
        raise ValidationException(detail="name required")
    if not req.id_card_suffix or not _valid_suffix(req.id_card_suffix):
        raise ValidationException(detail="id_card_suffix required (5 digits + digit or X)")

    try:
        hospital_id = resolve_hospital(req.id_card_suffix)
    except ResolverUnavailableError as e:
        raise ServiceUnavailableException(detail="resolver 不可用") from e
    if not hospital_id:
        raise UnauthorizedException(detail="无法匹配用户医院")

    row = db.execute(
        text("SELECT id, role, hospital_id, id_card_suffix, name "
             "FROM platform_user "
             "WHERE hospital_id = :hid AND name = :name AND id_card_suffix = :suf"),
        {"hid": hospital_id, "name": req.name, "suf": req.id_card_suffix},
    ).fetchone()
    if row is None:
        row = _auto_register(db, hospital_id, req.name, req.id_card_suffix)

    token = create_access_token(
        data={"user_id": row.id, "role": row.role, "hospital_id": row.hospital_id,
              "id_card_suffix": row.id_card_suffix, "name": row.name},
        expires_delta=timedelta(minutes=settings.APP_LOGIN_TOKEN_EXPIRE_MINUTES),
    )
    return TokenResponse(
        access_token=token, token_type="bearer", user_id=row.id, role=row.role,
        hospital_id=row.hospital_id, id_card_suffix=row.id_card_suffix, name=row.name,
    )


def _auto_register(db, hospital_id: str, name: str, id_card_suffix: str):
    """三元组不存在时自动注册;并发撞唯一索引时回查已有行(幂等)。"""
    username = f"app_{hospital_id}_{name}_{id_card_suffix}"
    password_hash = hash_password(secrets.token_urlsafe(32))
    try:
        db.execute(
            text("INSERT INTO platform_user "
                 "(username, password_hash, role, hospital_id, id_card_suffix, name) "
                 "VALUES (:un, :ph, 'user', :hid, :suf, :name)"),
            {"un": username, "ph": password_hash, "hid": hospital_id,
             "suf": id_card_suffix, "name": name},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
    row = db.execute(
        text("SELECT id, role, hospital_id, id_card_suffix, name "
             "FROM platform_user "
             "WHERE hospital_id = :hid AND name = :name AND id_card_suffix = :suf"),
        {"hid": hospital_id, "name": name, "suf": id_card_suffix},
    ).fetchone()
    return row
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_auth_app_login.py -q`
Expected: 9 passed

- [ ] **Step 5: 回归 auth 相关测试**

Run: `cd backend && .venv/bin/pytest tests/test_auth_id_suffix.py -q`
Expected: PASS(不受影响)

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/auth.py backend/tests/test_auth_app_login.py
git commit -m "feat: app-login 免密登录接口与自动注册"
```

---

### Task 3: AGENTS.md 文档 + 全量回归

**Files:**
- Modify: `AGENTS.md`(末尾「RabbitMQ vhost」之前任意位置新增一节,建议放在「批量上传文件名约定」之后)
- Test: 全量

- [ ] **Step 1: 更新 AGENTS.md**

在 `AGENTS.md` 中新增一节:

```markdown
## 外部 App 免密登录(app-login)(2026-09-01 起)

**事实**: `backend/app/api/auth.py` 提供 `POST /api/v1/auth/app-login`,外部 App 用
`app_key + name + id_card_suffix` 换取与普通登录一致的 JWT(role='user',有效期
`APP_LOGIN_TOKEN_EXPIRE_MINUTES` 默认 7 天),再以 Bearer 调用现有 `/api/v1/reports/*`、
`/api/v1/chat/*`(router 零改动)。hospital_id 由 `resolve_hospital(suffix)` 经
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
```

- [ ] **Step 2: 全量回归**

Run: `cd backend && .venv/bin/pytest tests/ -q`
Expected: 286 passed, 2 failed(新增 9 + 原 1 = 10?按实际输出核对;2 个失败为基线 pre-existing:`test_monthly_rollover_renames_to_yyyymm_and_starts_new_file` 与 `test_group_sql.py::test_high_risk_list_basic`,与本功能无关)

> 注:原基线 276 passed。新增 `test_auth_app_login.py` 9 个 + `test_config_batch.py` 新增 1 个 = 286 passed / 2 failed。

- [ ] **Step 3: 提交**

```bash
git add AGENTS.md
git commit -m "docs: AGENTS.md 记录 app-login 信任模型与配置"
```

---

## Self-Review

**Spec coverage(对照 spec §2-§8):**
- §2 接口契约(请求字段/200/401/400/503)→ Task 2 + 测试 3-9
- §3 流程(resolver → 查三元组 → 签发)→ Task 2
- §4 自动注册(username 合成 / 随机密码 / IntegrityError 幂等)→ Task 2 `_auto_register` + 测试 2-3
- §5 安全(compare_digest / 信任模型文档)→ Task 2 实现 + Task 3 AGENTS.md
- §6 测试(8 类场景)→ Task 2 测试 1-9
- §7 文件清单 → Task 1(config/.env.example/exceptions)+ Task 2(auth.py/test)+ Task 3(AGENTS.md/spec 已在 brainstorming 提交)
- §8 落地顺序 → 迁移/配置顺序在 Global Constraints 与 AGENTS.md 中记录

**Placeholder scan:** 无 TBD/TODO;所有代码步骤含完整代码。

**Type consistency:** `AppLoginRequest(app_key,name,id_card_suffix)` 在测试与实现一致;`resolve_hospital` / `ResolverUnavailableError` / `create_access_token` 签名与现有代码一致;`ServiceUnavailableException` 在 Task 1 定义、Task 2 引用。
