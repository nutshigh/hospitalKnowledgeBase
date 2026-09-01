# 接入 baUser searchUser 接口解析用户医院 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `hospital_resolver` 从「POST 后六位 → hospital_id」改为「GET searchUser(name+后六位) → orgId(=hospital_id)」,使批量上传与 app-login 真实解析用户医院。

**Architecture:** `hospital_resolver.resolve_hospital(name, id_suffix)` 发 `GET {EXTERNAL_RESOLVER_URL}?realName=&idCardLast6=`,解析 baUser 信封 `{code,msg,data}` 并对 data 数组做 `realName==name AND idCardLast6==id_suffix` 精确过滤,取唯一命中项的 `orgId` 返回 `str(orgId)`。两个调用方(`extract_worker` 批内缓存改复合 key、`app_login`)适配新签名。

**Tech Stack:** FastAPI + httpx;测试用 pytest + monkeypatch + fake httpx.Client stub(见 `tests/core/test_hospital_resolver.py` 现有风格)。

## Global Constraints

- 后端测试命令:`cd backend && .venv/bin/pytest tests/ -q`(基线 290 passed / 2 pre-existing failed,与本功能无关)
- `EXTERNAL_RESOLVER_URL` 指向 **searchUser 完整 URL**(如 `http://localhost:82/snowyApi/biz/baUserOpen/searchUser`),未配置 → `resolve_hospital` 返回 None
- orgId **即** hospital_id,直接 `str(orgId)` 返回,**不做映射**
- 信封:`{code: 200 成功, 500 失败, msg, data: [ {realName, idCardLast6, orgId}, ... ]}`;`data` 可为 null/空数组
- 精确过滤:`realName == name AND idCardLast6 == id_suffix`(全等);恰好 1 条 → 返回;≥2 条同 orgId → 返回该 orgId;≥2 条不同 orgId → None(warning)
- 错误分类:HTTP 4xx → None(warning);HTTP 5xx / 超时 / 传输错 / body `code!=200` / 坏 JSON → `ResolverUnavailableError`
- 本地租户不存在 → 短路 `hospital_not_found`(调用方逻辑不变,本 plan 不改 `_hospital_registered`)
- 禁止在 `backend/pyproject.toml` 加 vllm;不新增第三方依赖
- 无新增 DDL / 无迁移

---

### Task 1: resolver 契约改 `(name, id_suffix)` + 信封解析 + 精确过滤

**Files:**
- Modify: `backend/app/core/hospital_resolver.py`(整文件重写,见下)
- Test: `backend/tests/core/test_hospital_resolver.py`(整文件重写,见下)

**Interfaces:**
- Consumes: `settings.EXTERNAL_RESOLVER_URL` / `settings.EXTERNAL_RESOLVER_TIMEOUT`(已存在)
- Produces:
  - `resolve_hospital(name: str, id_suffix: str) -> Optional[str]`
  - `_parse_response(resp: httpx.Response, name: str, id_suffix: str) -> Optional[str]`
  - `_build_params(name: str, id_suffix: str) -> dict`
  - `ResolverUnavailableError`(已存在,语义不变)

- [ ] **Step 1: 重写测试文件**(`backend/tests/core/test_hospital_resolver.py`,整文件替换)

```python
"""hospital_resolver 单测:精确匹配 / 无匹配 / 歧义 / 业务错误 / 宕机 / 未配置。"""
import httpx
import pytest

from app.core import hospital_resolver


@pytest.fixture(autouse=True)
def _reset_client():
    hospital_resolver._shared_client = None
    yield
    hospital_resolver._shared_client = None


def test_resolve_hospital_matches(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "操作成功",
                    "data": [{"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002}]}

    def fake_get(url, params):
        assert url == "http://x/searchUser"
        assert params == {"realName": "张三", "idCardLast6": "12345X"}
        return FakeResp()

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(fake_get))
    assert hospital_resolver.resolve_hospital("张三", "12345X") == "1000002"


def test_resolve_hospital_exact_filter_ignores_others(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok",
                    "data": [
                        {"realName": "张三丰", "idCardLast6": "12345X", "orgId": 9000},
                        {"realName": "张三", "idCardLast6": "99999X", "orgId": 8000},
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002},
                    ]}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "12345X") == "1000002"


def test_resolve_hospital_no_match_empty_array(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok", "data": []}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "123456") is None


def test_resolve_hospital_null_data(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok", "data": None}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "123456") is None


def test_resolve_hospital_ambiguous_returns_none_and_warns(monkeypatch, caplog):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok",
                    "data": [
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002},
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000003},
                    ]}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    with caplog.at_level("WARNING", logger="app.batch.extract.resolver"):
        assert hospital_resolver.resolve_hospital("张三", "12345X") is None
    assert any("resolver ambiguous" in rec.message for rec in caplog.records)


def test_resolve_hospital_same_orgid_multi_records(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok",
                    "data": [
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002},
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002},
                    ]}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "12345X") == "1000002"


def test_resolve_hospital_business_code_500_raises(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 500, "msg": "内部错误", "data": None}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("张三", "123456")


def test_resolve_hospital_http_404_is_no_match(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    class FakeResp:
        status_code = 404
        def json(self):
            return {}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "123456") is None


def test_resolve_hospital_http_500_raises_unavailable(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    class FakeResp:
        status_code = 500
        def json(self):
            return {}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("张三", "123456")


def test_resolve_hospital_timeout_raises_unavailable(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/searchUser")

    def boom(url, params):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(boom))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("张三", "123456")


def test_resolve_hospital_url_not_configured_returns_none(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "")
    assert hospital_resolver.resolve_hospital("张三", "123456") is None


class _StubClient:
    def __init__(self, get_fn):
        self._get = get_fn
    def get(self, url, params=None):
        return self._get(url, params)
    @property
    def is_closed(self):
        return False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/core/test_hospital_resolver.py -q`
Expected: FAIL(旧实现按 `{"hospital_id"}` 解析,新断言全挂)

- [ ] **Step 3: 重写实现**(`backend/app/core/hospital_resolver.py`,整文件替换)

```python
"""身份证后六位 → hospital_id 的外部解析客户端(批量上传分发 + app-login 用)。

对接 baUser 开放接口 searchUser:
  GET {EXTERNAL_RESOLVER_URL}?realName={name}&idCardLast6={id_suffix}
统一信封 {code, msg, data},data 为数组 [{realName, idCardLast6, orgId}, ...]。
orgId 即 hospital_id(用户确认),直接 str(orgId) 返回;对 data 做精确过滤防串号。
"""
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("app.batch.extract.resolver")


class ResolverUnavailableError(Exception):
    """外部接口不可用(超时/5xx/业务 code!=200/坏 JSON)。调用方应走批次级重试,而非短路。"""


_shared_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.Client(timeout=settings.EXTERNAL_RESOLVER_TIMEOUT)
    return _shared_client


def _build_params(name: str, id_suffix: str) -> dict:
    return {"realName": name, "idCardLast6": id_suffix}


def _parse_response(resp: httpx.Response, name: str, id_suffix: str) -> Optional[str]:
    if resp.status_code != 200:
        if 400 <= resp.status_code < 500:
            logger.warning("resolver 4xx status=%s body=%s",
                           resp.status_code, getattr(resp, "text", "")[:200])
            return None  # 明确 not found → 无匹配
        raise ResolverUnavailableError(f"resolver http {resp.status_code}")
    try:
        payload = resp.json() or {}
    except ValueError:
        raise ResolverUnavailableError("resolver bad json")
    if payload.get("code") != 200:
        raise ResolverUnavailableError(
            f"resolver business code={payload.get('code')} msg={payload.get('msg')}")
    data = payload.get("data") or []
    if not isinstance(data, list):
        raise ResolverUnavailableError("resolver bad data shape")
    org_ids = {
        str(item["orgId"])
        for item in data
        if item.get("realName") == name and item.get("idCardLast6") == id_suffix
    }
    if not org_ids:
        return None
    if len(org_ids) > 1:
        logger.warning("resolver ambiguous name=%s suffix=%s org_ids=%s",
                       name, id_suffix, org_ids)
        return None
    return next(iter(org_ids))


def resolve_hospital(name: str, id_suffix: str) -> Optional[str]:
    """返回 hospital_id(匹配)/ None(明确无匹配)。宕机抛 ResolverUnavailableError。"""
    url = settings.EXTERNAL_RESOLVER_URL
    if not url:
        return None  # 未配置:默认无匹配,防误落库
    client = _get_client()
    try:
        resp = client.get(url, params=_build_params(name, id_suffix))
        return _parse_response(resp, name, id_suffix)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise ResolverUnavailableError(str(e)) from e
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/core/test_hospital_resolver.py -q`
Expected: 11 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/core/hospital_resolver.py backend/tests/core/test_hospital_resolver.py
git commit -m "feat: hospital_resolver 对接 baUser searchUser(姓名+后六位,orgId=hospital_id)"
```

---

### Task 2: extract_worker 传 name + 复合缓存 key

**Files:**
- Modify: `backend/app/modules/report/extract_worker.py:46-60`(`_resolve_hospital_id`)、`:180`、`:215`(调用点)
- Test: `backend/tests/test_extract_worker.py:89`、`:452`(resolver stub 签名)

**Interfaces:**
- Consumes: `hospital_resolver.resolve_hospital(name, id_suffix)`(Task 1)
- Produces: `_resolve_hospital_id(batch_id, name, id_suffix) -> Optional[str]`;缓存 `_batch_resolver_cache[batch_id][(name, id_suffix)]`

- [ ] **Step 1: 更新调用方测试 stub 签名**(`test_extract_worker.py`)

`test_extract_worker.py:89`:
```python
# 旧
hr_resolve = patch.object(_hr, "resolve_hospital", lambda suffix: "H001")
# 新
hr_resolve = patch.object(_hr, "resolve_hospital", lambda name, suffix: "H001")
```

`test_extract_worker.py:452`:
```python
# 旧
with patch.object(_hr, "resolve_hospital", lambda suffix: None):
# 新
with patch.object(_hr, "resolve_hospital", lambda name, suffix: None):
```

`test_extract_worker.py:477`(`side_effect=ResolverUnavailableError("down")`)无需改(side_effect 异常与参数无关)。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_extract_worker.py -q`
Expected: FAIL(TypeError: resolve_hospital() missing 1 required positional argument / 旧签名不匹配)

- [ ] **Step 3: 实现**(`extract_worker.py`)

a) 缓存 key 改复合(第 43 行注释 + 函数):

```python
# 批内缓存:batch_id → {(name, id_suffix): hospital_id | None};批结束清理
_batch_resolver_cache: dict[str, dict[tuple[str, str], Optional[str]]] = {}


def _resolve_hospital_id(batch_id, name, id_suffix) -> Optional[str]:
    cache = _batch_resolver_cache.setdefault(batch_id, {})
    key = (name, id_suffix)
    if key in cache:
        return cache[key]
    hospital_id = hospital_resolver.resolve_hospital(name, id_suffix)
    if hospital_id is None:
        cache[key] = None
        return None
    if not _hospital_registered(hospital_id):
        _log.warning("resolve hospital not registered batch=%s name=%s suffix=%s hid=%s",
                     batch_id, name, id_suffix, hospital_id)
        cache[key] = None
        return None
    cache[key] = hospital_id
    return hospital_id
```

b) 两处调用点(第 180 行 zip 分支、第 215 行 tar 分支):

```python
# 旧
file_hospital = _resolve_hospital_id(b.id, id_suffix)
# 新
file_hospital = _resolve_hospital_id(b.id, name, id_suffix)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_extract_worker.py -q`
Expected: 全过(含 T2.14 / T2.15)

- [ ] **Step 5: 提交**

```bash
git add backend/app/modules/report/extract_worker.py backend/tests/test_extract_worker.py
git commit -m "feat: extract_worker 传 name 解析医院,批内缓存改 (name,id_suffix) 复合 key"
```

---

### Task 3: app_login 传 name

**Files:**
- Modify: `backend/app/api/auth.py:177`
- Test: `backend/tests/test_auth_app_login.py:99`、`:201`、`:208`

**Interfaces:**
- Consumes: `resolve_hospital(name, id_suffix)`(Task 1);`app_login` 内局部 `name = req.name.strip()`(已有)
- Produces: 无新接口(app_login 行为不变,仅传参)

- [ ] **Step 1: 更新测试 stub 签名**(`test_auth_app_login.py`)

`:99`(ctx fixture):
```python
# 旧
monkeypatch.setattr(auth, "resolve_hospital", lambda suf: "H001")
# 新
monkeypatch.setattr(auth, "resolve_hospital", lambda name, suf: "H001")
```

`:201`:
```python
# 旧
auth.resolve_hospital = lambda suf: None
# 新
auth.resolve_hospital = lambda name, suf: None
```

`:208`:
```python
# 旧
def boom(suf):
# 新
def boom(name, suf):
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_auth_app_login.py -q`
Expected: FAIL(TypeError: resolve_hospital() missing positional argument / stub 签名不匹配)

- [ ] **Step 3: 实现**(`auth.py:177`)

```python
# 旧
hospital_id = resolve_hospital(req.id_card_suffix)
# 新
hospital_id = resolve_hospital(name, req.id_card_suffix)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_auth_app_login.py -q`
Expected: 13 passed

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/auth.py backend/tests/test_auth_app_login.py
git commit -m "feat: app_login 调 resolver 传姓名(兼容 baUser searchUser 契约)"
```

---

### Task 4: 文档 + 全量回归

**Files:**
- Modify: `backend/.env.example:72-75`(resolver 占位注释)
- Modify: `AGENTS.md`(resolver 契约描述)
- Test: 全量

- [ ] **Step 1: 更新 .env.example**

`backend/.env.example`,把现有 resolver 两行占位替换为:

```
# External resolver (baUser searchUser:GET ?realName=&idCardLast6= → orgId 即 hospital_id)
# EXTERNAL_RESOLVER_URL=http://localhost:82/snowyApi/biz/baUserOpen/searchUser
# EXTERNAL_RESOLVER_TIMEOUT=10
```

- [ ] **Step 2: 更新 AGENTS.md**

在「批量上传文件名约定」小节里,把 `failed_stage` 的 `hospital_not_found` 描述中「外部接口(`EXTERNAL_RESOLVER_URL`)无匹配」改为明确指向 baUser searchUser:

找到 `AGENTS.md` 中这一行:
```
- `hospital_not_found`:文件名格式合法,但外部接口(`EXTERNAL_RESOLVER_URL`)无匹配或解析出的 hospital_id 本地未注册。**不可重试**。
```
替换为:
```
- `hospital_not_found`:文件名格式合法,但外部接口(`EXTERNAL_RESOLVER_URL`,baUser searchUser)按 `realName+idCardLast6` 无精确匹配、解析出 orgId 本地未注册、或匹配歧义。**不可重试**。
```

并在「批量上传文件名约定」小节末尾追加一行契约说明:

```
- 外部接口契约:`GET {EXTERNAL_RESOLVER_URL}?realName={姓名}&idCardLast6={后六位}` → baUser 信封
  `{code,msg,data}`;data 数组按 `realName==姓名 AND idCardLast6==后六位` 精确过滤,唯一命中项
  `str(orgId)` 即 hospital_id(orgId 与本地 hospital_tenant.hospital_id 一致)。
```

- [ ] **Step 3: 全量回归**

Run: `cd backend && .venv/bin/pytest tests/ -q`
Expected: 290 passed, 2 failed(2 个失败为基线 pre-existing:`test_monthly_rollover_renames_to_yyyymm_and_starts_new_file` 与 `test_group_sql.py::test_high_risk_list_basic`)

- [ ] **Step 4: 提交**

```bash
git add backend/.env.example AGENTS.md
git commit -m "docs: resolver 契约改为 baUser searchUser(姓名+后六位→orgId)"
```

---

## Self-Review

**Spec coverage(对照 spec §0-§6):**
- §2 resolver 契约(GET query / 信封 / 精确过滤 / str(orgId))→ Task 1
- §2 错误分类(4xx None / 5xx+code!=200 ResolverUnavailableError)→ Task 1
- §3.1 extract_worker 传 name + 复合缓存 key → Task 2
- §3.2 app_login 传 name → Task 3
- §4 测试(6 新增 + 2 调用方适配)→ Task 1/2/3
- §7 文件清单 → Task 1-4;`.env.example` + AGENTS.md → Task 4

**Placeholder scan:** 无 TBD/TODO;所有代码步骤含完整代码。

**Type consistency:** `resolve_hospital(name, id_suffix)` 在 Task 1 定义、Task 2/3 调用一致;`_parse_response(resp, name, id_suffix)` 内部使用;`_StubClient.get(url, params=None)` 在全部测试 stub 一致;`org_ids` 集合保证去重(str(orgId) 后取唯一)。
