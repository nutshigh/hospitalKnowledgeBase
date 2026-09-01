# 批量上传按身份证后六位分发(外部接口解析医院) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 批量上传文件命名改为 `<姓名>_<身份证后六位>.<ext>`，通过可配置外部接口解析用户所在医院，`report_task/report_info/chat_session.user_id` 改存后六位字符串，登录/报告列表/user_profile/chat/统计/agent 全链路按后六位锚定。

**Architecture:** extract_worker 在解压循环内同步解析后六位 → 外部 resolver 接口(批内缓存)→ 本地租户校验 → 路由到目标医院 DB 落库。新增 `hospital_not_found` 失败阶段(不可重试)，接口宕机走既有 extract 批级重试。`platform_user` 新增 `id_card_suffix`，登录后放入 JWT 带出。

**Tech Stack:** Python 3.10 / FastAPI / SQLAlchemy / httpx / MySQL / pika (RabbitMQ) / pytest

## Global Constraints

- 外部接口契约未定：按最简约定实现 `POST body {"id_suffix": "..."} → resp {"hospital_id": "..."}`；`EXTERNAL_RESOLVER_URL` 配置项从 `.env` 读，默认 `""`(未配置时 resolver 返回 None，全部归 `hospital_not_found`)
- `EXTERNAL_RESOLVER_TIMEOUT` 默认 `10.0`
- 身份证后六位正则 `^([0-9]{5}[0-9X])$`（5 位数字 + 末位数字或 X），整体文件名正则 `^([^_]+)_([0-9]{5}[0-9X])$`
- `user_id` 存后六位字符串，列类型 `VARCHAR(16)`；存量数据不动
- `failed_stage` 取值扩展 `oversize` / `dispatch_unmatched` / `hospital_not_found` / `parsing` / `interpretation`；前三类不可重试
- 每个文件检查顺序：大小(oversize) → 文件名格式(dispatch_unmatched) → 医院解析(hospital_not_found)
- 批内缓存按 `batch_id` 作用域，处理完清理；模块级 `_batch_resolver_cache: dict[str, dict[str, Optional[str]]]`
- 运行测试：`cd backend && .venv/bin/pytest tests/ -q`
- 不修改 `start.sh` 的 GPU/venv/worker 进程数；只改 DDL 块与增量 ALTER
- 本实现不自动 commit（用户自行 commit）

---

### Task 1: 配置项 + hospital_resolver 模块

**Files:**
- Modify: `backend/app/config.py`（Settings 类内 Batch Import 段后新增两项）
- Create: `backend/app/core/hospital_resolver.py`
- Test: `backend/tests/core/test_hospital_resolver.py`（新建目录 `tests/core/`）

**Interfaces:**
- Consumes: `settings.EXTERNAL_RESOLVER_URL`、`settings.EXTERNAL_RESOLVER_TIMEOUT`
- Produces: `hospital_resolver.resolve_hospital(id_suffix: str) -> Optional[str]`；`hospital_resolver.ResolverUnavailableError`（供 Task 2/3 使用）

- [ ] **Step 1: 写配置字段**

`backend/app/config.py` 在 `BATCH_FILE_MAX_SIZE` 行(123)后新增：

```python
    # External hospital resolver (batch upload id-card suffix → hospital)
    # 空 = 未配置,resolve_hospital 一律返回 None(全部 hospital_not_found)
    EXTERNAL_RESOLVER_URL: str = ""
    EXTERNAL_RESOLVER_TIMEOUT: float = 10.0
```

- [ ] **Step 2: 写失败的 resolver 单测**

创建 `backend/tests/core/test_hospital_resolver.py`：

```python
"""hospital_resolver 单测:匹配 / 无匹配 / 宕机 / 未配置。"""
import httpx
import pytest

from app.core import hospital_resolver


@pytest.fixture(autouse=True)
def _reset_client():
    hospital_resolver._shared_client = None
    yield
    hospital_resolver._shared_client = None


def test_resolve_hospital_matches(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"hospital_id": "H001"}

    def fake_post(url, json):
        assert url == "http://x/r"
        assert json == {"id_suffix": "12345X"}
        return FakeResp()

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(fake_post))
    assert hospital_resolver.resolve_hospital("12345X") == "H001"


def test_resolve_hospital_no_match_returns_none(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"hospital_id": None}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, json: FakeResp()))
    assert hospital_resolver.resolve_hospital("123456") is None


def test_resolve_hospital_404_is_no_match(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    class FakeResp:
        status_code = 404
        def json(self):
            return {}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, json: FakeResp()))
    assert hospital_resolver.resolve_hospital("123456") is None


def test_resolve_hospital_500_raises_unavailable(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    class FakeResp:
        status_code = 500
        def json(self):
            return {}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, json: FakeResp()))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("123456")


def test_resolve_hospital_timeout_raises_unavailable(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    def boom(url, json):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(boom))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("123456")


def test_resolve_hospital_url_not_configured_returns_none(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "")
    assert hospital_resolver.resolve_hospital("123456") is None


class _StubClient:
    def __init__(self, post_fn):
        self._post = post_fn
    def post(self, url, json):
        return self._post(url, json)
    @property
    def is_closed(self):
        return False
```

- [ ] **Step 3: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/core/test_hospital_resolver.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.core.hospital_resolver'`）

- [ ] **Step 4: 实现 resolver 模块**

创建 `backend/app/core/hospital_resolver.py`：

```python
"""身份证后六位 → hospital_id 的外部解析客户端(批量上传分发用)。

契约暂定最简约定,接口文档后提供时只改 `_build_request` / `_parse_response`
两个函数内部即可,对外 `resolve_hospital` 签名保持不变。
"""
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("app.batch.extract.resolver")


class ResolverUnavailableError(Exception):
    """外部接口不可用(超时/5xx/网络错)。调用方应走批次级重试,而非短路。"""


_shared_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.Client(timeout=settings.EXTERNAL_RESOLVER_TIMEOUT)
    return _shared_client


def _build_request(id_suffix: str) -> dict:
    # 契约暂定:POST body {"id_suffix": "12345X"}。接口文档后提供时改这里。
    return {"id_suffix": id_suffix}


def _parse_response(resp: httpx.Response) -> Optional[str]:
    if resp.status_code != 200:
        if 400 <= resp.status_code < 500:
            return None  # 明确 not found → 无匹配
        raise ResolverUnavailableError(f"resolver http {resp.status_code}")
    try:
        data = resp.json() or {}
    except ValueError:
        raise ResolverUnavailableError("resolver bad json")
    return data.get("hospital_id") or None


def resolve_hospital(id_suffix: str) -> Optional[str]:
    """返回 hospital_id(匹配)/ None(明确无匹配)。宕机抛 ResolverUnavailableError。"""
    url = settings.EXTERNAL_RESOLVER_URL
    if not url:
        return None  # 未配置:默认无匹配,防误落库
    client = _get_client()
    try:
        resp = client.post(url, json=_build_request(id_suffix))
        return _parse_response(resp)
    except (httpx.TimeoutException, httpx.TransportError) as e:
        raise ResolverUnavailableError(str(e)) from e
```

- [ ] **Step 5: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/core/test_hospital_resolver.py -q`
Expected: 6 passed

---

### Task 2: extract_worker 文件名解析改为后六位

**Files:**
- Modify: `backend/app/modules/report/extract_worker.py:24-36`（正则 + `_parse_filename`）
- Test: `backend/tests/test_extract_worker.py`（替换 T2.10/T2.11 两个失效用例）

**Interfaces:**
- Consumes: 无
- Produces: `_parse_filename(filename: str) -> Optional[tuple[str, str]]` 返回 `(姓名, id_suffix)`（Task 3 使用）

- [ ] **Step 1: 写失败的解析单测**

在 `backend/tests/test_extract_worker.py` 中，把 `test_resolve_user_id_matches_three_segment`（303-308 行）与 `test_resolve_user_id_rejects_non_numeric_or_missing_segment`（314-321 行）整体替换为：

```python
# ---------------------------------------------------------------------------
# T2.10 _parse_filename: 姓名_身份证后六位 命中(末位可为 X)
# ---------------------------------------------------------------------------
def test_parse_filename_id_suffix_matches():
    from app.modules.report.extract_worker import _parse_filename
    assert _parse_filename("张三_123456.pdf") == ("张三", "123456")
    assert _parse_filename("李四_12345X.pdf") == ("李四", "12345X")
    assert _parse_filename("LiSi_204800.pdf") == ("LiSi", "204800")
    assert _parse_filename("sub/dir/王五_204800.jpg") == ("王五", "204800")


# ---------------------------------------------------------------------------
# T2.11 _parse_filename: 反例不命中(返回 None)
# ---------------------------------------------------------------------------
def test_parse_filename_id_suffix_rejects():
    from app.modules.report.extract_worker import _parse_filename
    assert _parse_filename("1001.pdf") is None               # 只 1 段
    assert _parse_filename("张三_12345.pdf") is None         # 末段 5 位
    assert _parse_filename("张三_1234567.pdf") is None       # 末段 7 位
    assert _parse_filename("张三_12345Y.pdf") is None        # 末位非法字符
    assert _parse_filename("张三_H001_1001.pdf") is None     # 旧 3 段格式废弃
    assert _parse_filename("张三_123456_extra.pdf") is None  # 3 段
    assert _parse_filename("张三H0011001.pdf") is None       # 无下划线
    assert _parse_filename(".pdf") is None                   # 空 basename
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_extract_worker.py -k "parse_filename" -v`
Expected: FAIL（`ImportError: cannot import name '_resolve_user_id'`，因为旧用例还引用已删符号；且新函数 `_parse_filename` 未实现新语义）

- [ ] **Step 3: 修改正则与解析函数**

`backend/app/modules/report/extract_worker.py:24-36`：

```python
# 文件名约定: <姓名>_<身份证后六位>.<ext>  (后六位 = 5 位数字 + 末位数字或 X)
_FILENAME_RE = re.compile(r"^([^_]+)_([0-9]{5}[0-9X])$")


def _parse_filename(filename: str) -> Optional[tuple[str, str]]:
    """从 zip/tar 内文件名抽取 (姓名, 身份证后六位)。
    Returns (str, str) on match, None on mismatch。
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    m = _FILENAME_RE.match(base)
    if m:
        return m.group(1), m.group(2)
    return None
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_extract_worker.py -k "parse_filename" -v`
Expected: 2 passed（test_parse_filename_id_suffix_matches / test_parse_filename_id_suffix_rejects）

---

### Task 3: extract_worker 医院解析 + 落库链路 + 失败短路

**Files:**
- Modify: `backend/app/modules/report/extract_worker.py`（新增 resolver 导入、`_resolve_hospital_id`、`_hospital_registered`、`_record_hospital_not_found`、改 `_stream_to_report` 签名与 zip/tar 分支、缓存清理）
- Modify: `backend/app/modules/report/service.py:16`（`create_task` 的 `user_id: int` → `str`）
- Test: `backend/tests/test_extract_worker.py`（env fixture 加 resolver patch + 缓存清理；适配 T2.1/T2.3/T2.4/T2.6/T2.12/T2.13；新增 hospital_not_found 与宕机重试用例）

**Interfaces:**
- Consumes: `hospital_resolver.resolve_hospital`、`hospital_resolver.ResolverUnavailableError`（Task 1）；`_parse_filename`（Task 2）
- Produces: `_resolve_hospital_id(batch_id, id_suffix) -> Optional[str]`；`_record_hospital_not_found(db, batch_id, file_path, size)`；`_stream_to_report(..., user_id: str, ...)`（Task 4 依赖 `hospital_not_found` 行与 `create_task(user_id: str)`）

- [ ] **Step 1: 适配 env fixture(resolver mock + 缓存清理)**

`backend/tests/test_extract_worker.py` 的 `env` fixture（53-89 行）加两个 patch 与缓存清理，使现有用例默认命中医院 H001：

```python
    from app.core import hospital_resolver as _hr
    hr_resolve = patch.object(_hr, "resolve_hospital", lambda suffix: "H001")
    hr_registered = patch("app.modules.report.extract_worker._hospital_registered",
                          lambda hid: True)
    ...
    hr_resolve.start(); hr_registered.start()
    try:
        yield s, tmp, Mq, msgs
    finally:
        hr_resolve.stop(); hr_registered.stop()
        from app.modules.report import extract_worker as _ew
        _ew._batch_resolver_cache.clear()
        ...
```

（在现有 `getdb_p` 等 patch 的 start/stop 块内同步加/停这两个 patch，并 `import app.modules.report.extract_worker as _ew; _ew._batch_resolver_cache.clear()` 放入 finally，先于其它 stop。）

- [ ] **Step 2: 新增两个失败用例**

在 `test_extract_worker.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# T2.14 后六位命中但外部接口无匹配 → file.failed_stage='hospital_not_found'
# ---------------------------------------------------------------------------
def test_T2_14_hospital_not_found(env):
    db, tmp, Mq, msgs = env
    from app.core import hospital_resolver as _hr
    with patch.object(_hr, "resolve_hospital", lambda suffix: None):
        ap = os.path.join(tmp, "a.zip")
        _make_zip(ap, [("张三_123456.pdf", b"x")])
        _make_batch(env, ap)
        from app.modules.report.extract_worker import handle_extract_task
        handle_extract_task(_msg(archive_path=ap))

    f = db.query(BatchImportFile).one()
    assert f.status == "failed"
    assert f.failed_stage == "hospital_not_found"
    assert f.error_message == "hospital_not_found"
    assert f.report_task_id is None        # 不 create_task
    assert len(msgs) == 0                  # 不投 parsing
    b1 = db.query(BatchImport).get("b1")
    assert b1.failed == 1
    assert b1.status == "partial_failed"


# ---------------------------------------------------------------------------
# T2.15 后六位命中但外部接口宕机 → 批次重试(publish_retry extract.bulk)
# ---------------------------------------------------------------------------
def test_T2_15_resolver_down_retries_batch(env):
    db, tmp, Mq, msgs = env
    from app.core import hospital_resolver as _hr
    from app.core.hospital_resolver import ResolverUnavailableError
    with patch.object(_hr, "resolve_hospital",
                      side_effect=ResolverUnavailableError("down")):
        ap = os.path.join(tmp, "a.zip")
        _make_zip(ap, [("张三_123456.pdf", b"x")])
        _make_batch(env, ap)
        from app.modules.report.extract_worker import handle_extract_task
        handle_extract_task(_msg(archive_path=ap))

    Mq.publish_retry.assert_called_once()
    args, kwargs = Mq.publish_retry.call_args
    assert args[0] == "extract.bulk"
    assert kwargs.get("batch_id") == "b1"
    import json as _json
    assert _json.loads(args[1])["payload"]["retry_count"] == 1
    db.refresh(db.query(BatchImport).get("b1"))
    assert db.query(BatchImport).get("b1").status == "extracting"
    assert len(msgs) == 0
```

- [ ] **Step 3: 运行确认新用例失败**

Run: `cd backend && .venv/bin/pytest tests/test_extract_worker.py -k "hospital_not_found or resolver_down" -v`
Expected: FAIL（`_resolve_hospital_id` 未实现 / `_record_hospital_not_found` 不存在）

- [ ] **Step 4: 实现 extract_worker 改动**

`backend/app/modules/report/extract_worker.py`：

a) 顶部导入新增：

```python
from sqlalchemy import text

from app.core import hospital_resolver
from app.core.database import get_hospital_db, get_template_db
```

（`get_template_db` 已由 `get_hospital_db` 同模块导出，直接替换现有 import 行：`from app.core.database import get_hospital_db` → 上述两行。）

b) 在 `_parse_filename` 之后、`handle_extract_task` 之前新增：

```python
# 批内缓存:batch_id → {id_suffix: hospital_id | None};批结束清理
_batch_resolver_cache: dict[str, dict[str, Optional[str]]] = {}


def _resolve_hospital_id(batch_id, id_suffix) -> Optional[str]:
    cache = _batch_resolver_cache.setdefault(batch_id, {})
    if id_suffix in cache:
        return cache[id_suffix]
    hospital_id = hospital_resolver.resolve_hospital(id_suffix)
    if hospital_id is None:
        cache[id_suffix] = None
        return None
    if not _hospital_registered(hospital_id):
        _log.warning("resolve hospital not registered batch=%s suffix=%s hid=%s",
                     batch_id, id_suffix, hospital_id)
        cache[id_suffix] = None
        return None
    cache[id_suffix] = hospital_id
    return hospital_id


def _hospital_registered(hospital_id: str) -> bool:
    """template 库 hospital_tenant 是否登记该医院且启用。"""
    db = next(get_template_db())
    try:
        row = db.execute(
            text("SELECT 1 FROM hospital_tenant WHERE hospital_id = :hid AND is_active = 1"),
            {"hid": hospital_id},
        ).fetchone()
        return row is not None
    finally:
        db.close()


def _record_hospital_not_found(db, batch_id, file_path, size):
    """记一行 file failed='hospital_not_found',既不落盘也不投 parsing。

    与 dispatch_unmatched 同等级短路:外部接口无匹配或解析出的医院本地未注册。
    """
    _log.info(
        "extract stage=hospital_not_found batch=%s file=%s size=%d",
        batch_id, file_path, size,
    )
    import uuid as _uuid
    fid = _uuid.uuid4().hex
    db.add(BatchImportFile(
        id=fid, batch_id=batch_id, file_path=file_path, file_size=size,
        crc32=f"hnf{_uuid.uuid4().hex[:5]}",
        status="failed", failed_stage="hospital_not_found",
        error_message="hospital_not_found",
    ))
    b = db.query(BatchImport).get(batch_id)
    if b is not None:
        b.failed = (b.failed or 0) + 1
    db.commit()
```

c) zip 分支（`_extract_and_enqueue` 内，114-126 行）替换解析段：

```python
                parsed = _parse_filename(info.filename)
                if parsed is None:
                    _record_dispatch_unmatched(db, b.id, info.filename, info.file_size)
                    continue
                _name, id_suffix = parsed
                file_hospital = _resolve_hospital_id(b.id, id_suffix)
                if file_hospital is None:
                    _record_hospital_not_found(db, b.id, info.filename, info.file_size)
                    continue
                file_db = next(get_hospital_db(file_hospital)) if file_hospital != hospital_id else db
                try:
                    with zf.open(info) as fh:
                        _stream_to_report(file_db, b, file_hospital, info.filename, fh,
                                          info.file_size, id_suffix, batch_db=db)
                finally:
                    if file_db is not db:
                        file_db.close()
```

d) tar 分支（145-157 行）同结构替换：

```python
                parsed = _parse_filename(member.name)
                if parsed is None:
                    _record_dispatch_unmatched(db, b.id, member.name, member.size)
                    continue
                _name, id_suffix = parsed
                file_hospital = _resolve_hospital_id(b.id, id_suffix)
                if file_hospital is None:
                    _record_hospital_not_found(db, b.id, member.name, member.size)
                    continue
                file_db = next(get_hospital_db(file_hospital)) if file_hospital != hospital_id else db
                try:
                    fh = tf.extractfile(member)
                    _stream_to_report(file_db, b, file_hospital, member.name, fh,
                                      member.size, id_suffix, batch_db=db)
                finally:
                    if file_db is not db:
                        file_db.close()
```

e) `handle_extract_task` finally（89-90 行 `db.close()` 前）加缓存清理：

```python
    finally:
        _batch_resolver_cache.pop(batch_id, None)
        db.close()
```

f) `_stream_to_report`（203-241 行）签名 `user_id: int` → `user_id: str`：

```python
def _stream_to_report(target_db, b, hospital_id, rel_path, fh, size, user_id: str,
                      batch_db=None):
```

（函数体不变；`create_task(user_id=user_id)` 传的是字符串后六位。）

g) `backend/app/modules/report/service.py:16`：

```python
def create_task(db: Session, hospital_id: str, user_id: str, file_path: str,
```

- [ ] **Step 5: 适配既有用例文件名**

`test_extract_worker.py` 中所有 zip/tar 内文件名从旧 3 段改成 `姓名_后六位`（env fixture 已 mock resolver→H001，故全部走正常落库）：

- T2.1(111): `("张三_123456.pdf", b"pdf1"), ("李四_123457.pdf", b"pdf2"), ("王五_123458.pdf", b"pdf3")`
- T2.2(135): `[("张三_123456.pdf", b"x" * 200)]`（oversize 检查在文件名解析前，文件名无所谓）
- T2.3(155-160): `("张三_123456.pdf", b"pdf"), ("李四_123457.jpg", b"jpg"), ("王五_123458.png", b"png"), ("skip1.docx", ...), ("skip2.txt", ...)`
- T2.4(180): `[("张三_123456.pdf", b"same"), ("李四_123457.pdf", b"same")]`
- T2.5(196): `[("张三_123456.pdf", b"x")]`
- T2.6(212): `[("张三_123456.pdf", b"x"), ("李四_123457.pdf", b"y")]`
- T2.7(234): 不变（损坏 zip 早退）
- T2.8(252): `[("张三_123456.pdf", b"x")]`（`_extract_and_enqueue` 被整体 patch，文件名不影响）
- T2.9(290): 同上
- T2.12(330): 不变（`report.pdf` 无下划线 → dispatch_unmatched）
- T2.13(353): `[("张三_123456.pdf", b"x"), ("李四_12345X.pdf", b"y")]`；断言改为：

```python
    tasks = db.query(ReportTask).order_by(ReportTask.id).all()
    assert len(tasks) == 2
    assert {t.user_id for t in tasks} == {"123456", "12345X"}
    assert "999" not in {t.user_id for t in tasks}
```

- [ ] **Step 6: 运行全量 extract 测试**

Run: `cd backend && .venv/bin/pytest tests/test_extract_worker.py -q`
Expected: 全绿（含 T2.14/T2.15 新增与 T2.1–T2.13 适配）

---

### Task 4: batch_service retry_failed 排除 hospital_not_found

**Files:**
- Modify: `backend/app/modules/report/batch_service.py:263`
- Test: `backend/tests/test_batch_service.py`（扩展 unretryable 测试）

**Interfaces:**
- Consumes: `failed_stage="hospital_not_found"` 的行（Task 3 写入）
- Produces: `retry_failed` 对三类 unretryable 的 `skipped_unretryable` 计数

- [ ] **Step 1: 写失败的扩展测试**

`backend/tests/test_batch_service.py` 追加：

```python
def test_retry_failed_skips_hospital_not_found(db):
    """hospital_not_found 无 report_task_id,重试应跳过并计数 skipped_unretryable。"""
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x",
                    archive_path="/x", status="partial_failed", failed=1)
    f_hnf = BatchImportFile(id="fhnf", batch_id="b1", file_path="/x/r.pdf",
                            file_size=1, crc32="cccc3333",
                            status="failed", failed_stage="hospital_not_found",
                            error_message="hospital_not_found")
    db.add_all([b, f_hnf]); db.commit()

    patcher, msgs = _mock_publish()
    try:
        r = BatchService.retry_failed(db, "b1")
        assert r["requeued"] == 0
        assert r["skipped_unretryable"] == 1
        db.refresh(f_hnf)
        assert f_hnf.status == "failed"
        assert msgs == []
        db.refresh(b)
        assert b.status == "partial_failed"
        assert b.failed == 1
    finally:
        patcher.stop()
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_batch_service.py -k hospital_not_found -v`
Expected: FAIL（当前 UNRETRYABLE_STAGES 不含 hospital_not_found → requeued=1 或异常）

- [ ] **Step 3: 修改 UNRETRYABLE_STAGES**

`backend/app/modules/report/batch_service.py:263`：

```python
        UNRETRYABLE_STAGES = ("oversize", "dispatch_unmatched", "hospital_not_found")
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_batch_service.py -q`
Expected: 全绿

---

### Task 5: ORM 模型 user_id 类型 + 新建租户 DDL

**Files:**
- Modify: `backend/app/modules/report/models.py:9,29`
- Modify: `backend/app/modules/chat/models.py:9`
- Modify: `start.sh:114-115,124`（新建租户 DDL 块）
- Test: 无新增（类型变化由现有测试回归覆盖）

**Interfaces:**
- Produces: ORM 列类型 `String(16)`，SQLAlchemy 写库为字符串；DDL 新租户建 `VARCHAR(16)`（存量租户由 Task 9 迁移脚本处理）

- [ ] **Step 1: 改 report ORM**

`backend/app/modules/report/models.py:9` 与 `:29`：

```python
    user_id = Column(String(16), nullable=False)
```

（两处 `BigInteger` → `String(16)`。）

- [ ] **Step 2: 改 chat ORM**

`backend/app/modules/chat/models.py:9`：

```python
    user_id = Column(String(16), nullable=False)
```

- [ ] **Step 3: 改新建租户 DDL**

`start.sh:114` `report_task` 的 `user_id BIGINT NOT NULL` → `user_id VARCHAR(16) NOT NULL`；
`start.sh:115` `report_info` 同理；
`start.sh:124` `chat_session` 的 `user_id BIGINT NOT NULL` → `user_id VARCHAR(16) NOT NULL`。

- [ ] **Step 4: 回归验证**

Run: `cd backend && .venv/bin/pytest tests/test_batch_models.py tests/test_batch_service.py tests/test_extract_worker.py -q`
Expected: 全绿（sqlite 上 String 列写 "123456" 字符串正常）

---

### Task 6: 认证链路 id_card_suffix(登录带出 + 注册接口)

**Files:**
- Modify: `backend/app/core/dependencies.py:12-36`（CurrentUser + get_current_user）
- Modify: `backend/app/api/auth.py`（RegisterRequest/TokenResponse/login/register）
- Modify: `backend/scripts/create_test_user.py`（可选带后缀）
- Test: `backend/tests/test_auth_id_suffix.py`（新建）

**Interfaces:**
- Consumes: 无
- Produces: `CurrentUser.id_card_suffix: Optional[str]`；JWT claim `id_card_suffix`；`register` 请求体可选字段 `id_card_suffix`（role='user' 必填）

- [ ] **Step 1: 写失败的认证测试**

创建 `backend/tests/test_auth_id_suffix.py`：

```python
"""id_card_suffix 认证链路:登录带出 / 注册必填 / 后端校验。"""
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import CurrentUser, get_current_user


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/whoami")
    def whoami(current_user: CurrentUser = Depends(get_current_user)):
        return {
            "user_id": current_user.user_id,
            "id_card_suffix": current_user.id_card_suffix,
        }

    return TestClient(app)


def test_current_user_carries_id_card_suffix(client):
    token = _make_token(user_id=5, role="user", hospital_id="H001",
                        id_card_suffix="12345X")
    with patch("app.core.dependencies.decode_access_token", return_value=token):
        resp = client.get("/whoami", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert resp.json()["id_card_suffix"] == "12345X"


def test_current_user_without_suffix_is_none(client):
    token = _make_token(user_id=5, role="doctor", hospital_id="H001",
                        id_card_suffix=None)
    with patch("app.core.dependencies.decode_access_token", return_value=token):
        resp = client.get("/whoami", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert resp.json()["id_card_suffix"] is None


def _make_token(user_id, role, hospital_id, id_card_suffix):
    return {
        "user_id": user_id, "role": role, "hospital_id": hospital_id,
        "id_card_suffix": id_card_suffix,
    }
```

（`app` 与 `auth` 路由不在此测试范围，聚焦 CurrentUser 带出。register 的字段校验测试放 Task 8 回归前手动验证或后续补充。）

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && .venv/bin/pytest tests/test_auth_id_suffix.py -q`
Expected: FAIL（`CurrentUser` 无 `id_card_suffix` 属性）

- [ ] **Step 3: 改 dependencies**

`backend/app/core/dependencies.py:12-36`：

```python
class CurrentUser:
    def __init__(self, user_id: int, role: str, hospital_id: Optional[str] = None,
                 id_card_suffix: Optional[str] = None):
        self.user_id = user_id
        self.role = role
        self.hospital_id = hospital_id
        self.id_card_suffix = id_card_suffix


async def get_current_user(
    authorization: str = Header(..., description="Bearer <token>"),
    db: Session = Depends(get_template_db),
) -> CurrentUser:
    if not authorization.startswith("Bearer "):
        raise UnauthorizedException(detail="Invalid authorization header")
    token = authorization[7:]
    payload = decode_access_token(token)
    if payload is None:
        raise UnauthorizedException(detail="Invalid or expired token")
    user_id = payload.get("user_id")
    role = payload.get("role")
    hospital_id = payload.get("hospital_id")
    id_card_suffix = payload.get("id_card_suffix")
    if not user_id or not role:
        raise UnauthorizedException(detail="Invalid token payload")
    if hospital_id:
        set_current_hospital_id(hospital_id)
    return CurrentUser(user_id=user_id, role=role, hospital_id=hospital_id,
                       id_card_suffix=id_card_suffix)
```

- [ ] **Step 4: 改 auth 路由**

`backend/app/api/auth.py`：

```python
class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str
    hospital_id: str | None = None
    id_card_suffix: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    role: str
    hospital_id: str | None = None
    id_card_suffix: str | None = None
```

`login`（34-54 行）：SELECT 加列、token 带 claim：

```python
    row = db.execute(
        text("SELECT id, password_hash, role, hospital_id, id_card_suffix "
             "FROM platform_user WHERE username = :un AND is_active = 1"),
        {"un": req.username},
    ).fetchone()

    if not row or not verify_password(req.password, row.password_hash):
        raise UnauthorizedException(detail="Invalid username or password")

    token = create_access_token(data={
        "user_id": row.id,
        "role": row.role,
        "hospital_id": row.hospital_id,
        "id_card_suffix": row.id_card_suffix,
    })
    return TokenResponse(
        access_token=token,
        user_id=row.id,
        role=row.role,
        hospital_id=row.hospital_id,
        id_card_suffix=row.id_card_suffix,
    )
```

`register`（57-89 行）：role='user' 时校验后六位与 hospital_id，INSERT 带列：

```python
@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_template_db)):
    if req.role not in ("user", "doctor", "admin"):
        raise ValidationException(detail="Invalid role")

    if req.role == "user":
        if not req.hospital_id:
            raise ValidationException(detail="hospital_id required for role=user")
        if not req.id_card_suffix or not _valid_suffix(req.id_card_suffix):
            raise ValidationException(
                detail="id_card_suffix required (5 digits + digit or X) for role=user")

    existing = db.execute(
        text("SELECT id FROM platform_user WHERE username = :un"), {"un": req.username}
    ).fetchone()
    if existing:
        raise ValidationException(detail="Username already exists")

    db.execute(
        text("INSERT INTO platform_user "
             "(username, password_hash, role, hospital_id, id_card_suffix) "
             "VALUES (:un, :ph, :r, :hid, :suf)"),
        {"un": req.username, "ph": hash_password(req.password),
         "r": req.role, "hid": req.hospital_id, "suf": req.id_card_suffix},
    )
    db.commit()

    row = db.execute(
        text("SELECT id, role, hospital_id, id_card_suffix "
             "FROM platform_user WHERE username = :un"),
        {"un": req.username},
    ).fetchone()

    token = create_access_token(data={
        "user_id": row.id,
        "role": row.role,
        "hospital_id": row.hospital_id,
        "id_card_suffix": row.id_card_suffix,
    })
    return TokenResponse(
        access_token=token,
        user_id=row.id,
        role=row.role,
        hospital_id=row.hospital_id,
        id_card_suffix=row.id_card_suffix,
    )
```

文件顶部新增校验函数（`auth.py` 模块级）：

```python
import re

_SUFFIX_RE = re.compile(r"^[0-9]{5}[0-9X]$")


def _valid_suffix(s: str) -> bool:
    return bool(_SUFFIX_RE.match(s))
```

`me`（92-99 行）：返回带 `id_card_suffix`：

```python
@router.get("/me", response_model=TokenResponse)
def me(current_user: CurrentUser = Depends(get_current_user)):
    return TokenResponse(
        access_token="",
        user_id=current_user.user_id,
        role=current_user.role,
        hospital_id=current_user.hospital_id,
        id_card_suffix=current_user.id_card_suffix,
    )
```

- [ ] **Step 5: 改 create_test_user 脚本(可选)**

`backend/scripts/create_test_user.py`：新增第 5 个位置参数 `id_card_suffix`；INSERT 语句带 `id_card_suffix` 列；打印后缀。

- [ ] **Step 6: 运行确认通过**

Run: `cd backend && .venv/bin/pytest tests/test_auth_id_suffix.py tests/test_report_router_streamed.py -q`
Expected: 全绿

---

### Task 7: report service/router user_id 字符串化

**Files:**
- Modify: `backend/app/modules/report/service.py:273-278`（`list_reports` 签名与过滤）
- Modify: `backend/app/modules/report/router.py:95-96,63-64`（list 过滤用后缀；upload 落库用后缀）
- Test: `backend/tests/test_report_router_streamed.py`（适配 CurrentUser 构造与断言）

**Interfaces:**
- Consumes: `CurrentUser.id_card_suffix`（Task 6 定义）；`create_task(user_id: str)`（Task 3）
- Produces: `list_reports(db, hospital_id, user_id: Optional[str], ...)`

- [ ] **Step 1: 改 list_reports**

`backend/app/modules/report/service.py:273-278`：

```python
def list_reports(db: Session, hospital_id: str, user_id: Optional[str] = None,
                 page: int = 1, page_size: int = 20) -> tuple:
    from sqlalchemy.orm import joinedload
    q = db.query(ReportInfo)
    if user_id:
        q = q.filter(ReportInfo.user_id == user_id)
```

（仅签名类型注解 `Optional[int]` → `Optional[str]`，逻辑不变。）

- [ ] **Step 2: 改 router**

`backend/app/modules/report/router.py:95-96`：

```python
    user_id = None if current_user.role != "user" else current_user.id_card_suffix
```

`backend/app/modules/report/router.py:63-64`（upload 落库）：改用后缀；医生/admin 无后缀则回退平台 user_id（存字符串）：

```python
    task = service.create_task(
        db=db, hospital_id=current_user.hospital_id,
        user_id=current_user.id_card_suffix if current_user.role == "user"
        else str(current_user.user_id),
        file_path=file_path, filename=file.filename, file_type=file_type,
        file_size=size,
    )
```

- [ ] **Step 3: 适配 report 测试**

`backend/tests/test_report_router_streamed.py`：`CurrentUser(user_id=1, role="user", hospital_id="H001")` → 增加 `id_card_suffix="123456"`（23 行与 108 行）。若断言 `task.user_id == 1`，改为 `"123456"`。

- [ ] **Step 4: 运行确认**

Run: `cd backend && .venv/bin/pytest tests/test_report_router_streamed.py -q`
Expected: 全绿（Task 6 已定义 CurrentUser.id_card_suffix，可直接消费）

---

### Task 8: 下游全链路适配(user_profile / chat / agent / schemas)

**Files:**
- Modify: `backend/app/modules/user_profile/service.py`（`_auto_select_baseline:24`、`get_overview:41`、`get_comparison:276`、`get_ai_summary:326` 的 `user_id: int` → `str`）
- Modify: `backend/app/modules/user_profile/router.py:29,39,49`（传 `current_user.id_card_suffix`）
- Modify: `backend/app/modules/chat/service.py:13,22,47,56,64,101`（`user_id: int` → `str`）
- Modify: `backend/app/modules/chat/router.py:37,46,55,69,82,93,106,110`（传 `current_user.id_card_suffix`）
- Modify: `backend/app/modules/chat/schemas.py:12`（`SessionResponse.user_id: int` → `str`）
- Modify: `backend/app/ai/agents/chat_graph.py:239,266,274`（`run_chat_agent user_id: int` → `str`；`AgentContext` 传后缀）
- Modify: `backend/app/ai/agents/chat_planner.py:71`（`run_planner user_id: Optional[int]` → `Optional[str]`）
- Modify: `backend/app/ai/agents/tools.py:23`（`AgentContext.user_id: Optional[int]` → `Optional[str]`）
- Modify: `backend/app/ai/agents/interp_graph.py:91,200,574`（`InterpState.user_id`、`_fetch_trend`、invoke 传后缀）
- Modify: `backend/app/modules/interpretation/schemas.py:60`（`HighRiskItem.user_id: int` → `str`）
- Modify: `backend/app/modules/statistics/group_schemas.py:58`（`HighRiskItem.user_id: int` → `str`）
- Test: 适配 `backend/tests/user_profile/test_service.py`、`backend/tests/ai/agents/test_chat_planner.py`、`backend/tests/ai/agents/test_tools.py`、`backend/tests/ai/agents/test_interp_graph.py`、`backend/tests/modules/statistics/test_group_service.py`、`backend/tests/test_interp_worker_bulk.py`、`backend/tests/test_report_worker_bulk.py`

**Interfaces:**
- Consumes: `CurrentUser.id_card_suffix`（Task 6）；`report_info.user_id` 为字符串（Task 5）
- Produces: 全链路 `user_id` 为 `str`，无残余 `int` 语义

- [ ] **Step 1: user_profile 改签名**

`backend/app/modules/user_profile/service.py`：`user_id: int` → `user_id: str`（第 24/41/276/326 行函数签名；函数体不变）。`backend/app/modules/user_profile/router.py`：3 处 `current_user.user_id` → `current_user.id_card_suffix`。

- [ ] **Step 2: chat 改签名**

`backend/app/modules/chat/service.py`：`user_id: int` → `user_id: str`（13/22/47/56/64/101 行）。`backend/app/modules/chat/router.py`：8 处 `current_user.user_id` → `current_user.id_card_suffix`。`backend/app/modules/chat/schemas.py:12`：

```python
    user_id: str
```

- [ ] **Step 3: agent 层改类型**

`backend/app/ai/agents/tools.py:23`：

```python
    user_id: Optional[str] = None
```

`backend/app/ai/agents/chat_planner.py:71`：`user_id: Optional[int]` → `Optional[str]`（函数体不变，SQL 绑定字符串）。`backend/app/ai/agents/chat_graph.py:239`：`user_id: int` → `user_id: str`；266 行 `AgentContext(..., user_id=user_id)` 不变（类型已 str）。

`backend/app/ai/agents/interp_graph.py:91`：`user_id: int` → `user_id: str`；`:200` `_fetch_trend(user_id: str, db)`；`:574`：

```python
            "user_id": str(report.user_id or ""),
```

- [ ] **Step 4: 响应 schema 改类型**

`backend/app/modules/interpretation/schemas.py:60` 与 `backend/app/modules/statistics/group_schemas.py:58`：

```python
    user_id: str
```

- [ ] **Step 5: 适配受影响测试**

将下列测试中的 int user_id 改为字符串后六位（保持测试语义不变）：

- `backend/tests/user_profile/test_service.py`：`user_id=10` → `"123456"`，`user_id=20` → `"123457"`，`user_id=30` → `"123458"`，`user_id=99`/`999` → 对应字符串（如 `"999999"`）；`get_overview(db, user_id=999)` → `get_overview(db, user_id="999999")`；`get_ai_summary/get_comparison` 同理。
- `backend/tests/ai/agents/test_chat_planner.py:92,122,136,156`：`AgentContext(..., user_id=4)` → `user_id="123456"`
- `backend/tests/ai/agents/test_tools.py:43-45`：`user_id=2` → `user_id="123456"`
- `backend/tests/ai/agents/test_interp_graph.py:203,224,253`：`"user_id": 1` → `"user_id": "123456"`
- `backend/tests/modules/statistics/test_group_service.py:227,231,235,259,309,316,357`：dict 中 `"user_id": 1/2/3/i/i*10` → 字符串（如 `"1001"`/`"1002"`/`"1003"`/`str(i*10)`）
- `backend/tests/test_interp_worker_bulk.py:71`：`ReportInfo(user_id=1)` → `ReportInfo(user_id="123456")`
- `backend/tests/test_report_worker_bulk.py:63`：`ReportTask(..., user_id=1, ...)` → `user_id="123456"`
- `backend/tests/test_batch_service.py:97,101,145,279`：`ReportTask(user_id=1)` / `ReportInfo(user_id=1)` → `user_id="123456"`

- [ ] **Step 6: 运行全量测试**

Run: `cd backend && .venv/bin/pytest tests/ -q`
Expected: 全绿（若个别断言涉及 int→str 的 schema 校验失败，按 Step 5 模式修正）

---

### Task 9: 存量库迁移脚本 + template DDL

**Files:**
- Create: `backend/scripts/manual_migrations/003_user_id_suffix.sql`（按库运行说明）
- Modify: `infra/mysql/init/01_template_db.sql`（`platform_user` 加列，供新建环境）
- Modify: `start.sh`（存量初始化分支加增量 ALTER）

**Interfaces:**
- Consumes: 无
- Produces: 存量租户库 `report_task/report_info/chat_session.user_id` → `VARCHAR(16)`；`platform_user.id_card_suffix` 列存在

- [ ] **Step 1: 新建 template 加列 DDL**

`infra/mysql/init/01_template_db.sql` 在 `platform_user` 建表语句后追加（新建环境幂等）：

```sql
ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS id_card_suffix VARCHAR(8) NULL COMMENT '身份证后六位(终端用户锚定)';
```

- [ ] **Step 2: 存量库迁移 SQL**

创建 `backend/scripts/manual_migrations/003_user_id_suffix.sql`：

```sql
-- 003: 批量上传按身份证后六位分发(存量库迁移)
-- 用法:对每个存量 tenant 库(hospital_<id>)与 hospital_template 分别执行。

-- 每个 hospital_<id> 库:
ALTER TABLE report_task MODIFY user_id VARCHAR(16) NOT NULL;
ALTER TABLE report_info MODIFY user_id VARCHAR(16) NOT NULL;
ALTER TABLE chat_session MODIFY user_id VARCHAR(16) NOT NULL;

-- hospital_template 库:
ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS id_card_suffix VARCHAR(8) NULL;
```

- [ ] **Step 3: start.sh 存量初始化分支加 ALTER**

`start.sh` 的 `else` 分支（`log "数据库已初始化"` 后、现有 `failed_stage` ALTER 附近）追加：

```bash
  # 批量上传按身份证后六位分发:user_id 列改字符串(兼容旧库)
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE report_task MODIFY user_id VARCHAR(16) NOT NULL;" 2>/dev/null || true
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE report_info MODIFY user_id VARCHAR(16) NOT NULL;" 2>/dev/null || true
  docker exec hospital-mysql mysql -uroot -proot hospital_H001 -e \
    "ALTER TABLE chat_session MODIFY user_id VARCHAR(16) NOT NULL;" 2>/dev/null || true
  docker exec hospital-mysql mysql -uroot -proot hospital_template -e \
    "ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS id_card_suffix VARCHAR(8) NULL;" 2>/dev/null || true
```

- [ ] **Step 4: 校验迁移脚本语法**

Run: `cd backend && .venv/bin/python -c "import pathlib,subprocess; print(pathlib.Path('scripts/manual_migrations/003_user_id_suffix.sql').read_text())"`
Expected: 打印 SQL 无语法异常（不连库执行，仅人工核对）

---

### Task 10: 文档更新(AGENTS.md + 旧 spec 标注)

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: 更新 AGENTS.md 新租户表清单段落**

在「批量上传新增表」相关说明后，更新 `failed_stage` 取值段：

```markdown
`failed_stage` 已知取值:`parsing` / `interpretation` / `oversize` / `dispatch_unmatched` / `hospital_not_found`。
- `oversize`:单文件 > 50MB,无 `report_task_id`,**不可重试**。
- `dispatch_unmatched`:批量上传时文件名不符合 `<姓名>_<身份证后六位>.<ext>` 约定(两段下划线、末段 5 位数字 + 末位 0-9/X)。**不可重试**。
- `hospital_not_found`:文件名格式合法,但外部接口(`EXTERNAL_RESOLVER_URL`)无匹配或解析出的 hospital_id 本地未注册。**不可重试**。
```

- [ ] **Step 2: 更新 AGENTS.md 文件名约定**

新增小节：

```markdown
## 批量上传文件名约定(2026-09-01 起)

- 命名:`<姓名>_<身份证后六位>.<ext>`,后六位 = 5 位数字 + 末位数字或 X(校验位)。
- 用户锚定:一切以身份证后六位为准,`report_task/report_info/chat_session.user_id` 存后六位字符串(VARCHAR(16))。
- `platform_user.id_card_suffix` 存登录用户后六位,登录后经 JWT 带出;报告列表/档案/chat 按后六位过滤。
- 外部接口:`EXTERNAL_RESOLVER_URL` 配置,契约见 `docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md §3`。
- 旧 `<姓名>_<医院编号>_<用户编号>` 三段命名已废弃;存量数据 user_id 仍为旧数字 ID,不迁移(只影响新数据)。
```

- [ ] **Step 3: 旧 spec 标注废弃**

`docs/superpowers/specs/2026-07-16-batch-dispatch-by-filename-design.md` 顶部加一行状态注释：

```markdown
> **已废弃(2026-09-01)**:命名约定改为 `<姓名>_<身份证后六位>`,见 `2026-09-01-batch-upload-idcard-suffix-design.md`。
```

- [ ] **Step 4: 回归确认**

Run: `cd backend && .venv/bin/pytest tests/ -q`
Expected: 全绿（文档改动不影响测试，仅作最终回归）

---

## Self-Review

**Spec coverage:**
- §2 命名约定/正则 → Task 2
- §3 resolver 模块 → Task 1
- §4 extract_worker(缓存/租户校验/hospital_not_found/落库)→ Task 3
- §5 failed_stage + 重试 → Task 3 + Task 4
- §6 表结构 + 迁移脚本 → Task 5 + Task 9
- §7 登录/下游全链路 → Task 6(登录) + Task 7(report) + Task 8(下游)
- §8 注册接口 → Task 6
- §9 缺失资源(外部接口契约、注册入口、迁移)→ Task 1 / Task 6 / Task 9
- §12 测试 → 各 Task 内联 TDD
- §13 文档/AGENTS.md → Task 10

**Placeholder scan:** 无 TBD/TODO；所有代码块完整。

**Type consistency:** `_parse_filename` 返回 `tuple[str,str]`(Task 2) 与 Task 3 解包一致；`_resolve_hospital_id(batch_id, id_suffix)`(Task 3) 签名统一；`create_task(user_id: str)`(Task 3) 与 `_stream_to_report(user_id: str)` 一致；`CurrentUser.id_card_suffix`(Task 6) 被 Task 7/8 消费；`list_reports(user_id: Optional[str])`(Task 7) 与 Task 6 的 `id_card_suffix` 字符串匹配。

---

## 增补执行段:姓名 + 后六位双锚定(Tasks 11-15,最终 review 阶段用户确认)

> 前置:Task 1-10 全部完成。本段在其上叠加姓名维度,不改动后六位的存储/解析链路。
> 设计依据:`docs/superpowers/specs/2026-09-01-batch-upload-idcard-suffix-design.md §15`。

---

### Task 11: DDL + chat 模型加 name 列

**Files:**
- Modify: `backend/app/modules/chat/models.py`（ChatSession 加 `name`）
- Modify: `infra/mysql/init/01_template_db.sql`（platform_user 加 name）
- Modify: `backend/scripts/manual_migrations/003_user_id_suffix.sql`
- Modify: `start.sh`（else-branch + 新建租户 DDL 的 platform_user/chat_session 加 name）

**变更:**
```python
# chat/models.py ChatSession 加一列
    name = Column(String(50), nullable=True)
```
```sql
-- platform_user 加 name
ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS name VARCHAR(50) NULL COMMENT '登录姓名(与报告文件名姓名段一致)';
-- chat_session 加 name
ALTER TABLE chat_session ADD COLUMN IF NOT EXISTS name VARCHAR(50) NULL;
```
- 新建租户 DDL 块:platform_user 建表语句加 `name VARCHAR(50) DEFAULT NULL`;chat_session 加 `name VARCHAR(50) DEFAULT NULL`
- 迁移脚本 003 同步追加以上两条 ALTER

**验证:** `cd backend && .venv/bin/pytest tests/ -q`(256 passed / 2 pre-existing)

---

### Task 12: 认证链路加 name(register 唯一性 + login + CurrentUser)

**Files:**
- Modify: `backend/app/api/auth.py`
- Modify: `backend/app/core/dependencies.py`
- Modify: `backend/scripts/create_test_user.py`
- Test: `backend/tests/test_auth_id_suffix.py`(扩展)

**变更:**
1. `RegisterRequest` 加 `name: str | None = None`
2. `register`:role='user' 时 name 必填;唯一性校验 `(hospital_id, name, id_card_suffix)` 已存在则 `ValidationException`;INSERT 带 name
3. `login`:SELECT 加 `name`;JWT claim 加 `name`;`TokenResponse` 加 `name`
4. `CurrentUser` 加 `name: Optional[str] = None`;`get_current_user` 从 token 解出
5. `/me` 返回 name

**测试:**
- register 拒绝重复 `(hospital_id, name, id_card_suffix)`
- login 返回/JWT 带 name;CurrentUser 携带 name

**验证:** `cd backend && .venv/bin/pytest tests/test_auth_id_suffix.py -q`(扩展后全绿)

---

### Task 13: report 侧双锚定 + create_task 带姓名

**Files:**
- Modify: `backend/app/modules/report/service.py`(create_task 加 name 参数、process_task 空时填充、list_reports 双条件)
- Modify: `backend/app/modules/report/router.py`(list/upload 传 name)
- Test: `backend/tests/test_extract_worker.py`、`backend/tests/test_report_router_streamed.py`

**变更:**
1. `create_task(db, hospital_id, user_id, name=None, file_path, ...)`:创建 `ReportInfo(task_id, user_id, name=name)`
2. `process_task`(service.py:113):`report.name = personal_info.get("name")` → 改为仅在 `report.name` 为空时赋值
3. `list_reports(db, hospital_id, user_id=None, name=None, ...)`:user_id 命中时再加 `ReportInfo.name == name` 过滤
4. router list:`user_id = suffix if role=="user" else None; name = current_user.name if role=="user" else None`
5. router upload:单文件上传 `create_task(..., name=None)`(保留 VLM 解析)

**验证:** extract_worker 与 report_router_streamed 全绿;全量回归

---

### Task 14: 批量上传文件名姓名段落库

**Files:**
- Modify: `backend/app/modules/report/extract_worker.py`
- Test: `backend/tests/test_extract_worker.py`

**变更:**
1. `_stream_to_report` 签名加 `name: str`;调用 `create_task(..., name=name)`
2. zip/tar 两分支:不再丢弃姓名段,`name, id_suffix = parsed` 传入 `_stream_to_report`

**测试:** 新增/适配:批量落库后 `report_info.name == 文件名姓名段`(T2.13 扩展)

**验证:** `cd backend && .venv/bin/pytest tests/test_extract_worker.py -q` 全绿

---

### Task 15: user_profile + chat 双锚定 + AGENTS.md

**Files:**
- Modify: `backend/app/modules/user_profile/service.py` / `router.py`
- Modify: `backend/app/modules/chat/service.py` / `router.py`
- Modify: `AGENTS.md`
- Test: 适配 `backend/tests/user_profile/test_service.py`、`backend/tests/ai/agents/*`(如有)、`backend/tests/test_*.py` 相关

**变更:**
1. user_profile:`get_overview/get_comparison/get_ai_summary` 加 `name` 参数,过滤 `ReportInfo.user_id == uid AND ReportInfo.name == name`;router 传 `current_user.name`
2. chat:`create_session` 存 `user_id + name`;`list_sessions/get_session` 过滤 `ChatSession.user_id == uid AND ChatSession.name == name`;router 传 `current_user.id_card_suffix` + `current_user.name`
3. AGENTS.md:「批量上传文件名约定(2026-09-01 起)」补姓名维度:锚定 = 姓名 + 后六位,`platform_user.name` / `chat_session.name` 说明

**验证:** 全量 `cd backend && .venv/bin/pytest tests/ -q`(256 passed / 2 pre-existing)

---

## 增补段 Self-Review

- §15.2 七项 → Task 11-15 全覆盖(DDL/Task11、认证/Task12、report/Task13、批量落库/Task14、下游+文档/Task15)
- `create_task(name=...)` 默认 None 保持单文件上传 VLM 解析兼容
- `CurrentUser.name` 默认 None 保持既有测试调用兼容
- 存量 chat_session / platform_user 行 name 为 NULL:双条件匹配对新会话/新用户生效,存量按「存量不动」原则
