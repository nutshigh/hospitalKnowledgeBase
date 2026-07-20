# 批量上传报告分发到用户(按文件名约定) 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让管理员 role=admin 在 doctor-portal 上传 zip/tar 批量报告,后端按文件名约定 `<姓名>_<医院编号>_<用户编号>.<ext>` 解析出每份文件归属的终端用户 user_id,正确分发到该用户名下;命名不合规的文件标记 `failed_stage='dispatch_unmatched'` 不解析、UI 显示且禁用重试按钮。

**Architecture:** 沿用现有 batch 基础设施。在 `extract_worker` 解压遍历内增加一道 `_resolve_user_id(filename)` 短路:命中 → `create_task(user_id=<resolved>)`;不命中 → 直接写一行 `BatchImportFile.failed_stage='dispatch_unmatched'`,不投 parsing。`BatchService.retry_failed` 跳过 `oversize`/`dispatch_unmatched` 两类 unretryable;`get_progress` exposing `failed_stage` 给前端。前端在 doctor-portal 新增 `/batch` 路由 + 菜单 + BatchUploadPage(上传轮询 + failing_files 表 + unretryable Tag 重试按钮禁用)。

**Tech Stack:** Python 3.10 + FastAPI + SQLAlchemy + pytest(SQLite);React 18 + TypeScript + antd v5 + zustand + axios。无新增 dependency。

## Global Constraints

- 文件名约定:**3 段以半角 `_` 分隔,索引 2 必须是纯十进制数字**;正则 `^([^_]+)_([^_]+)_(\d+)$` 应用到 basename 去扩展名后的字符串
- 扩展名白名单仍为 `pdf|doc|jpg|jpeg|png`(不含 docx,沿用 `extract_worker.ALLOWED_EXTS`)
- 不查 `hospital_user` DB 校验 user_id 存在性(spec §1 D2 范围外)
- tenant 隔离由 JWT + `batch_router._db` 已保证;文件名内 `<医院编号>` 段忽略
- 不改 `start.sh` / DDL / venv / vLLM / GPU / worker 进程数
- 测试基线 168 passed(目标 ≥ 173 passing)
- 提交风格:`feat(batch): ...` / `fix(batch): ...` 沿用现有

---

## File Structure

**后端(修改):**
- `backend/app/modules/report/extract_worker.py` — 新增 `_resolve_user_id`、`_record_dispatch_unmatched`;改 `_extract_and_enqueue` 顺序、`_stream_to_report` 签名
- `backend/app/modules/report/batch_service.py` — `retry_failed` 短路 unretryable + 返回 `skipped_unretryable`;`get_progress` failing_files 加 `failed_stage`
- `backend/tests/test_extract_worker.py` — 新增 4 用例
- `backend/tests/test_batch_service.py` — 新增 1 用例
- `backend/tests/test_batch_router.py` — 适配 `skipped_unretryable` 字段

**前端(新增/修改):**
- `frontend/packages/doctor-portal/src/pages/BatchUploadPage.tsx` — 新建整页
- `frontend/packages/doctor-portal/src/router.tsx` — 加 `/batch` 路由 + `RoleGuard`
- `frontend/packages/doctor-portal/src/components/DoctorLayout.tsx` — 菜单加 `📦 批量上传分发`(条件渲染)
- `frontend/packages/doctor-portal/src/stores/doctorStore.ts` — 持久化 role/userId/hospitalId 防刷新丢失

**文档:**
- `AGENTS.md` — 末尾补一行 `dispatch_unmatched` 失败阶段说明

---

## Task 1: 后端 — `_resolve_user_id` + 反例正则测试

**Files:**
- Modify: `backend/app/modules/report/extract_worker.py:1-20`(顶部 import + 新函数)
- Test: `backend/tests/test_extract_worker.py`(新增 2 测试)

**Interfaces:**
- Produces: `_resolve_user_id(filename: str) -> Optional[int]` 已可在测试中直接 import 与单测

- [ ] **Step 1: 写失败测试 — 正面 + 反面**

在 `backend/tests/test_extract_worker.py` 末尾追加:

```python
# ---------------------------------------------------------------------------
# T2.10 _resolve_user_id: 三段命名命中
# ---------------------------------------------------------------------------
def test_resolve_user_id_matches_three_segment():
    from app.modules.report.extract_worker import _resolve_user_id
    assert _resolve_user_id("张三_H001_1001.pdf") == 1001
    assert _resolve_user_id("LiSi_H002_2048.pdf") == 2048
    # 路径前缀 OK(basename 拆解)
    assert _resolve_user_id("sub/dir/王五_H003_7.pdf") == 7


# ---------------------------------------------------------------------------
# T2.11 _resolve_user_id: 反例不命中(返回 None)
# ---------------------------------------------------------------------------
def test_resolve_user_id_rejects_non_numeric_or_missing_segment():
    from app.modules.report.extract_worker import _resolve_user_id
    assert _resolve_user_id("1001.pdf") is None              # 只 1 段
    assert _resolve_user_id("张三_1001.pdf") is None         # 只 2 段
    assert _resolve_user_id("张三_H001_abc.pdf") is None     # 末段非数字
    assert _resolve_user_id("张三_H001_1001_extra.pdf") is None  # 4 段
    assert _resolve_user_id("张三H0011001.pdf") is None      # 无下划线
    assert _resolve_user_id(".pdf") is None                 # 空 basename
```

- [ ] **Step 2: 跑测试验证它们 FAIL**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_extract_worker.py::test_resolve_user_id_matches_three_segment tests/test_extract_worker.py::test_resolve_user_id_rejects_non_numeric_or_missing_segment -v
```
Expected: FAIL with `ImportError: cannot import name '_resolve_user_id'`

- [ ] **Step 3: 写最小实现**

在 `backend/app/modules/report/extract_worker.py` 顶部 import 块后(`ALLOWED_EXTS = ...` 行后)新增:

```python
import re
from typing import Optional

# 文件名约定: <姓名>_<医院编号>_<用户编号>.<ext>
# basename 去扩展后,3 段半角下划线分隔,末段纯数字 = user_id。
_FILENAME_RE = re.compile(r"^([^_]+)_([^_]+)_(\d+)$")


def _resolve_user_id(filename: str) -> Optional[int]:
    """从 zip/tar 内文件名抽取终端用户 user_id。
    Returns int user_id on match, None on mismatch。
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    m = _FILENAME_RE.match(base)
    return int(m.group(3)) if m else None
```

(`re` 与 `Optional` 顶部已有的话直接补;若已 import 过 `re` 跳过)

- [ ] **Step 4: 跑测试验证 PASS**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_extract_worker.py::test_resolve_user_id_matches_three_segment tests/test_extract_worker.py::test_resolve_user_id_rejects_non_numeric_or_missing_segment -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/modules/report/extract_worker.py tests/test_extract_worker.py
git commit -m "feat(batch): _resolve_user_id 文件名约定解析 + 正反例测试"
```

---

## Task 2: 后端 — `_record_dispatch_unmatched` + `_extract_and_enqueue` 短路 + `_stream_to_report` 签名

**Files:**
- Modify: `backend/app/modules/report/extract_worker.py`(整个解压循环)
- Test: `backend/tests/test_extract_worker.py`(新增 2 集成用例,适配 1 现有用例)

**Interfaces:**
- Consumes: `_resolve_user_id`(Task 1)
- Produces: `_record_dispatch_unmatched(db, batch_id, file_path, size, reason="dispatch_unmatched")`;`_stream_to_report` 多 `user_id: int` 形参

- [ ] **Step 1: 写失败测试 — 不命中命名 → dispatch_unmatched**

在 `backend/tests/test_extract_worker.py` 末尾追加:

```python
# ---------------------------------------------------------------------------
# T2.12 zip 内文件名不合规 → file.failed_stage='dispatch_unmatched',不投 parsing
# ---------------------------------------------------------------------------
def test_T2_12_dispatch_unmatched(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("report.pdf", b"x")])  # 无下划线
    _make_batch(env, ap)

    from app.modules.report.extract_worker import handle_extract_task
    handle_extract_task(_msg(archive_path=ap))

    f = db.query(BatchImportFile).one()
    assert f.status == "failed"
    assert f.failed_stage == "dispatch_unmatched"
    assert f.error_message == "dispatch_unmatched"
    assert f.report_task_id is None        # 不 create_task
    assert len(msgs) == 0                  # 不投 parsing
    b1 = db.query(BatchImport).get("b1")
    assert b1.failed == 1
    assert b1.status == "partial_failed"   # 全 unmatched → partial_failed


# ---------------------------------------------------------------------------
# T2.13 命中三段命名 → report_task.user_id == 文件名第 3 段(而非上传者 b.user_id)
# ---------------------------------------------------------------------------
def test_T2_13_dispatch_uses_filename_user_id(env):
    db, tmp, Mq, msgs = env
    ap = os.path.join(tmp, "a.zip")
    _make_zip(ap, [("张三_H001_1001.pdf", b"x"), ("李四_H002_2048.pdf", b"y")])
    _make_batch(env, ap, user_id="999")  # 上传者 admin user_id=999

    from app.modules.report.extract_worker import handle_extract_task
    handle_extract_task(_msg(archive_path=ap))

    from app.modules.report.models import ReportTask
    tasks = db.query(ReportTask).order_by(ReportTask.id).all()
    assert len(tasks) == 2
    # 第一份 → 1001;第二份 → 2048 (no admin 999)
    assert {t.user_id for t in tasks} == {1001, 2048}
    assert 999 not in {t.user_id for t in tasks}
    file_rows = db.query(BatchImportFile).order_by(BatchImportFile.id).all()
    # file_path 顺序与 zip 一致(file_path 拼接顺序为 zip 内顺序)
    name_to_uid = {f.file_path: f.report_task_id for f in file_rows}
    for f in file_rows:
        assert f.report_task_id is not None
    assert len(msgs) == 2
```

- [ ] **Step 2: 跑测试验证 FAIL**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_extract_worker.py::test_T2_12_dispatch_unmatched tests/test_extract_worker.py::test_T2_13_dispatch_uses_filename_user_id -v
```
Expected: FAIL — `assert f.failed_stage == "dispatch_unmatched"` 报 AttributeError / 不存在;`{999}` 而非 `{1001, 2048}`(现状行为写 admin)

- [ ] **Step 3: 实现 `_record_dispatch_unmatched`**

在 `_record_oversize` 函数后新增(注意:不复用 `handle_extracted_file` 防止 `(batch_id,crc32)` 唯一约束吞同 size 行):

```python
def _record_dispatch_unmatched(db, batch_id, file_path, size,
                                reason="dispatch_unmatched"):
    """记一行 file failed,既不落盘也不投 parsing(与 oversize 同等级短路)。

    直接新建一行 BatchImportFile(uuid 主键,占位唯一 crc 不参与批内去重),
    复用 handle_extracted_file 会因占位 crc 把两个同 size unmatched 文件误去重。
    """
    import uuid as _uuid
    fid = _uuid.uuid4().hex
    db.add(BatchImportFile(
        id=fid, batch_id=batch_id, file_path=file_path, file_size=size,
        crc32=f"unm{_uuid.uuid4().hex[:8]}",
        status="failed", failed_stage=reason, error_message=reason,
    ))
    b = db.query(BatchImport).get(batch_id)
    if b is not None:
        b.failed = (b.failed or 0) + 1
    db.commit()
```

- [ ] **Step 4: 改 `_stream_to_report` 签名**

把 `_stream_to_report(db, b, hospital_id, rel_path, fh, size)` 改为多一个 `user_id: int` 形参,把内部 `create_task` 调用的 `user_id=int(b.user_id) if str(b.user_id).isdigit() else 0` 改用传入参数:

```python
def _stream_to_report(db, b, hospital_id, rel_path, fh, size, user_id: int):
    """读流,算 crc32,落盘,建 report_task 并 publish parsing.bulk(幂等)。"""
    data = fh.read()
    crc = f"{zlib.crc32(data) & 0xffffffff:08x}"
    fid = BatchService.handle_extracted_file(db, b.id, rel_path, crc, size)
    f = db.query(BatchImportFile).get(fid)
    if f is None:
        return
    if f.report_task_id is not None:
        return  # 已发布(幂等命中,F18 补差),不再 publish
    # 落盘至 FILE_STORAGE_ROOT/<h>/batch/extracted/<batch_id>/<fid>.<ext>
    ext = os.path.splitext(rel_path)[1].lstrip(".")
    extract_dir = os.path.join(os.path.dirname(b.archive_path), "extracted", b.id)
    os.makedirs(extract_dir, exist_ok=True)
    disk_path = os.path.join(extract_dir, f"{fid}.{ext}")
    with open(disk_path, "wb") as out:
        out.write(data)
    file_type = {"pdf": "pdf", "doc": "docx", "jpg": "image",
                 "jpeg": "image", "png": "image"}.get(ext, "image")
    from app.modules.report.service import create_task
    task = create_task(
        db=db, hospital_id=hospital_id,
        user_id=user_id,   # ← 不再是 b.user_id
        file_path=disk_path, filename=os.path.basename(rel_path),
        file_type=file_type, file_size=size, priority="bulk",
        batch_id=b.id, file_id=fid,
    )
    f.report_task_id = task.id
    db.commit()
```

- [ ] **Step 5: 改 `_extract_and_enqueue` — 插入 user_id 短路**

把 zip 分支的:

```python
if info.file_size > settings.BATCH_FILE_MAX_SIZE:
    _record_oversize(db, b.id, info.filename, info.file_size)
    continue
cum_uncompressed += info.file_size
if cum_uncompressed > 5 * archive_size:
    _record_oversize(db, b.id, info.filename, info.file_size)
    continue
with zf.open(info) as fh:
    _stream_to_report(db, b, hospital_id, info.filename, fh, info.file_size)
```

改为(在 oversize 检查后、`with zf.open` 前加 `_resolve_user_id` 短路):

```python
if info.file_size > settings.BATCH_FILE_MAX_SIZE:
    _record_oversize(db, b.id, info.filename, info.file_size)
    continue
cum_uncompressed += info.file_size
if cum_uncompressed > 5 * archive_size:
    _record_oversize(db, b.id, info.filename, info.file_size)
    continue
user_id = _resolve_user_id(info.filename)
if user_id is None:
    _record_dispatch_unmatched(db, b.id, info.filename, info.file_size)
    continue
with zf.open(info) as fh:
    _stream_to_report(db, b, hospital_id, info.filename, fh,
                      info.file_size, user_id)
```

tar 分支同样改成(同样的 user_id 短路,`tf.extractfile` 后 `_stream_to_report` 多传 `user_id`):

```python
if member.size > settings.BATCH_FILE_MAX_SIZE:
    _record_oversize(db, b.id, member.name, member.size)
    continue
cum_uncompressed += member.size
if cum_uncompressed > 5 * archive_size:
    _record_oversize(db, b.id, member.name, member.size)
    continue
user_id = _resolve_user_id(member.name)
if user_id is None:
    _record_dispatch_unmatched(db, b.id, member.name, member.size)
    continue
fh = tf.extractfile(member)
_stream_to_report(db, b, hospital_id, member.name, fh, member.size, user_id)
```

- [ ] **Step 6: 跑新测试验证 PASS**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_extract_worker.py::test_T2_12_dispatch_unmatched tests/test_extract_worker.py::test_T2_13_dispatch_uses_filename_user_id -v
```
Expected: PASS

- [ ] **Step 7: 跑整体 extract_worker + batch 套件,确认无回归**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_extract_worker.py tests/test_batch_service.py tests/test_batch_router.py tests/test_batch_sweeper.py tests/test_batch_models.py tests/test_report_worker_bulk.py tests/test_interp_worker_bulk.py -q
```
Expected: PASS(没新失败)。注意现有 T2.1 用的文件名是 `a.pdf` / `b.pdf` / `c.pdf`(无下划线),改造后这些都会被标 `dispatch_unmatched`,需要把这些测试更新为合规文件名(下一步处理)。

- [ ] **Step 8: 适配现有用例的文件名**

修改 `backend/tests/test_extract_worker.py` 中所有 zip/tar 内文件名为合规三段命名,使现有断言通过:

| 旧文件名 | 新文件名 | 影响用例 |
|---------|----------|---------|
| `a.pdf`, `b.pdf`, `c.pdf` | `u1_H001_1.pdf`, `u2_H001_2.pdf`, `u3_H001_3.pdf` | T2.1(3 pdf → 3 file 行 + 3 publish) |
| `big.pdf` | `big_H001_999.pdf`(保持 oversize 触发,文件名合规以便仅 oversize 触发) | T2.2 |
| `ok1.pdf`, `ok2.jpg`, `ok3.png` | `ok1_H001_1.pdf`, `ok2_H001_2.jpg`, `ok3_H001_3.png` | T2.3 |
| `a.pdf`, `b.pdf`(同内容) | `dup1_H001_1.pdf`, `dup2_H001_2.pdf`(内容同 → 同 crc) | T2.4 |
| `a.pdf`(订正:`b.pdf` 改成 `dup2_...`) | 同上 | T2.4 |
| `a.pdf` (cancelled) | `cn_H001_1.pdf` | T2.5 |
| `a.pdf`, `b.pdf`(requeue) | `q1_H001_1.pdf`, `q2_H001_2.pdf` | T2.6 |
| `a.pdf`(corrupt zip 文件名不会读到,跳过) | 不变 | T2.7 |
| `a.pdf`(transient) | `t1_H001_1.pdf` | T2.8, T2.9 |

每个用例改完 `git diff` 检查没有遗漏。

- [ ] **Step 9: 重跑全部 extract_worker 测试**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_extract_worker.py -v
```
Expected: 所有 T2.1 ~ T2.13 PASS

- [ ] **Step 10: Commit**

```bash
cd backend && git add app/modules/report/extract_worker.py tests/test_extract_worker.py
git commit -m "feat(batch): extract_worker 按文件名分发 user_id + dispatch_unmatched 短路"
```

---

## Task 3: 后端 — `BatchService.retry_failed` 短路 unretryable

**Files:**
- Modify: `backend/app/modules/report/batch_service.py:219-271`(`retry_failed`)
- Test: `backend/tests/test_batch_service.py`(新增 1 用例)

**Interfaces:**
- Produces: `retry_failed(...)` 返回 `{"requeued": int, "skipped_unretryable": int}`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_batch_service.py` 末尾追加:

```python
def test_retry_failed_skips_dispatch_unmatched_and_oversize(db):
    """retry_failed 对 oversize / dispatch_unmatched 两类 unretryable
    短路跳过,不计入 requeued,只在 skipped_unretryable 计数。
    no report_task_id 的两类不会 publish。"""
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x",
                    archive_path="/x", status="partial_failed", failed=2)
    f_unm = BatchImportFile(id="funm", batch_id="b1", file_path="/x/report.pdf",
                            file_size=1, crc32="aaaa1111",
                            status="failed", failed_stage="dispatch_unmatched",
                            error_message="dispatch_unmatched")
    f_oversize = BatchImportFile(id="fovz", batch_id="b1", file_path="/x/big.pdf",
                                 file_size=99, crc32="bbbb2222",
                                 status="failed", failed_stage="oversize",
                                 error_message="oversize")
    db.add_all([b, f_unm, f_oversize]); db.commit()

    patcher, msgs = _mock_publish()
    try:
        r = BatchService.retry_failed(db, "b1")
        assert r["requeued"] == 0
        assert r["skipped_unretryable"] == 2
        # 两个 file 行的 status 仍是 failed(未被重置为 queued)
        db.refresh(f_unm); db.refresh(f_oversize)
        assert f_unm.status == "failed"
        assert f_oversize.status == "failed"
        # 没有 publish
        assert msgs == []
        # batch 状态未变 partial_failed,failed 未扣减
        db.refresh(b)
        assert b.status == "partial_failed"
        assert b.failed == 2
    finally:
        patcher.stop()


def test_retry_failed_mixed_retryable_and_unretryable(db):
    """混合场景:1 个 dispatch_unmatched + 1 个 parsing 失败 → 重投 1,
    skipped_unretryable=1, batchFailed 扣减 1。"""
    from app.modules.report.models import ReportTask
    b = BatchImport(id="b1", hospital_id="H", user_id="u", filename="x",
                    archive_path="/x", status="partial_failed", failed=2)
    f_unm = BatchImportFile(id="funm", batch_id="b1", file_path="/x/report.pdf",
                            file_size=1, crc32="aaaa1111", status="failed",
                            failed_stage="dispatch_unmatched",
                            error_message="dispatch_unmatched")
    task = ReportTask(id=100, user_id=1, original_file_path="/x/a.pdf",
                      original_filename="a.pdf", file_type="pdf", file_size=1,
                      status="failed", priority=0, retry_count=3,
                      error_message="parse boom")
    db.add(b); db.add(task); db.commit()
    f_parse = BatchImportFile(id="fparse", batch_id="b1",
                              file_path="/x/a.pdf", file_size=1,
                              crc32="bbbb2222", status="failed",
                              failed_stage="parsing",
                              report_task_id=task.id,
                              error_message="parse boom")
    db.add_all([f_unm, f_parse]); db.commit()

    patcher, msgs = _mock_publish()
    try:
        r = BatchService.retry_failed(db, "b1")
        assert r["requeued"] == 1
        assert r["skipped_unretryable"] == 1
        # parsing 失败被重投;report_task 重置
        parse_msgs = [m for m in msgs if m.task_type == "parsing"]
        assert len(parse_msgs) == 1
        db.refresh(task)
        assert task.status == "queued" and task.retry_count == 0
        # dispatch_unmatched 文件保持 failed
        db.refresh(f_unm)
        assert f_unm.status == "failed"
        # batch status 从 partial_failed → parsing(也 requeued>0 触发)
        db.refresh(b)
        assert b.status == "parsing"
        assert b.failed == 1  # 扣减了 1(requeued=1)
    finally:
        patcher.stop()
```

- [ ] **Step 2: 跑测试验证 FAIL**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_batch_service.py::test_retry_failed_skips_dispatch_unmatched_and_oversize tests/test_batch_service.py::test_retry_failed_mixed_retryable_and_unretryable -v
```
Expected: FAIL — `KeyError: 'skipped_unretryable'` 或者 requeued 包含了 unretryable 数量

- [ ] **Step 3: 改 `retry_failed`**

把 `backend/app/modules/report/batch_service.py` 的 `retry_failed` 整体替换为:

```python
@staticmethod
def retry_failed(db: Session, batch_id: str, file_ids: Optional[list] = None) -> dict:
    b = db.query(BatchImport).get(batch_id)
    if b is None or b.status == "cancelled":
        raise ValueError("batch not retryable")
    q = db.query(BatchImportFile).filter_by(batch_id=batch_id, status="failed")
    if file_ids:
        q = q.filter(BatchImportFile.id.in_(file_ids))
    files = q.all()
    requeued = 0
    skipped_unretryable = 0
    saw_interp = False
    UNRETRYABLE_STAGES = ("oversize", "dispatch_unmatched")
    for f in files:
        stage = f.failed_stage
        if stage in UNRETRYABLE_STAGES:
            # 这两类无 report_task_id,重试无意义;跳过且不重置状态。
            skipped_unretryable += 1
            continue
        f.status = "queued"
        f.error_message = None
        f.failed_stage = None
        if stage == "interpretation":
            # 解读失败: parse 仍 OK, 只重置 ReportInterpretation 重投
            # interpretation.bulk, 不动 ReportTask.retry_count。
            report_id = BatchService._report_id_for_file(db, f)
            if report_id is not None:
                BatchService._reset_interp_for_retry(db, report_id)
                rabbitmq.publish(TaskMessage(
                    task_type="interpretation", hospital_id=b.hospital_id,
                    priority="bulk",
                    payload={"report_id": report_id, "hospital_id": b.hospital_id,
                             "batch_id": batch_id, "file_id": f.id},
                ))
                saw_interp = True
        else:
            # parsing: 有 report_task_id 才重投
            if f.report_task_id:
                from app.modules.report.models import ReportTask
                t = db.query(ReportTask).get(f.report_task_id)
                if t:
                    t.status = "queued"
                    t.retry_count = 0
                    rabbitmq.publish(TaskMessage(
                        task_type="parsing", hospital_id=b.hospital_id,
                        priority="bulk",
                        payload={"task_id": t.id, "hospital_id": b.hospital_id,
                                 "file_path": t.original_file_path, "batch_id": batch_id,
                                 "file_id": f.id},
                    ))
        requeued += 1
    b.failed = max(0, (b.failed or 0) - requeued)
    if b.status == "partial_failed" and requeued > 0:
        b.status = "interpreting" if saw_interp else "parsing"
    b.updated_at = datetime.now(timezone.utc)
    db.commit()
    BatchService._maybe_advance_status(db, b)
    return {"requeued": requeued, "skipped_unretryable": skipped_unretryable}
```

- [ ] **Step 4: 跑测试验证 PASS**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_batch_service.py::test_retry_failed_skips_dispatch_unmatched_and_oversize tests/test_batch_service.py::test_retry_failed_mixed_retryable_and_unretryable -v
```
Expected: PASS

- [ ] **Step 5: 适配 `test_batch_router.py::test_T11_5_retry_failed_partial`**

该测试断言 `r.json() == {"requeued": 1}`(整 dict 比较会因新增 `skipped_unretryable` 不通过)。改为更细的断言:

把 `backend/tests/test_batch_router.py:213-215`:

```python
    r = env["client"].post("/api/v1/reports/batches/rb/retry", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"requeued": 1}
```

改为:

```python
    r = env["client"].post("/api/v1/reports/batches/rb/retry", json={})
    assert r.status_code == 200, r.text
    assert r.json()["requeued"] == 1
    assert r.json()["skipped_unretryable"] == 0
```

- [ ] **Step 6: 跑整体 batch 套件**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_batch_service.py tests/test_batch_router.py -q
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/modules/report/batch_service.py tests/test_batch_service.py tests/test_batch_router.py
git commit -m "feat(batch): retry_failed 短路 oversize/dispatch_unmatched + skipped_unretryable 计数"
```

---

## Task 4: 后端 — `get_progress` 暴露 `failed_stage`

**Files:**
- Modify: `backend/app/modules/report/batch_service.py:198-217`(`get_progress`)
- Test: `backend/tests/test_batch_router.py`(新增 1 endpoint 用例)

**Interfaces:**
- Produces: `GET /batches/{id}` 返回的 `failing_files[].failed_stage`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_batch_router.py` 末尾追加:

```python
def test_T11_progress_exposes_failed_stage(env):
    """failing_files 项包含 failed_stage 字段,供前端区分可/不可重试。"""
    s = env["Session"]()
    b = BatchImport(id="bfs", hospital_id="H001", user_id="1", filename="x.zip",
                    archive_path="/x.zip", status="partial_failed", total=2, failed=2)
    f1 = BatchImportFile(id="f_1", batch_id="bfs", file_path="/r.pdf",
                         file_size=1, crc32="aaaa1111", status="failed",
                         failed_stage="dispatch_unmatched",
                         error_message="dispatch_unmatched")
    f2 = BatchImportFile(id="f_2", batch_id="bfs", file_path="/b.pdf",
                         file_size=1, crc32="bbbb2222", status="failed",
                         failed_stage="parsing", error_message="parse boom")
    s.add_all([b, f1, f2]); s.commit(); s.close()

    g = env["client"].get("/api/v1/reports/batches/bfs")
    assert g.status_code == 200
    ff = g.json()["failing_files"]
    stages = {x["id"]: x["failed_stage"] for x in ff}
    assert stages["f_1"] == "dispatch_unmatched"
    assert stages["f_2"] == "parsing"
```

- [ ] **Step 2: 跑测试验证 FAIL**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_batch_router.py::test_T11_progress_exposes_failed_stage -v
```
Expected: FAIL — `KeyError: 'failed_stage'` or assertion fail

- [ ] **Step 3: 改 `get_progress`**

在 `backend/app/modules/report/batch_service.py` 的 `get_progress` 里,`failing_files` 列表推导加 `failed_stage`:

把:
```python
"failing_files": [
    {"id": f.id, "file_path": f.file_path, "error_message": f.error_message}
    for f in failing
],
```

改为:
```python
"failing_files": [
    {"id": f.id, "file_path": f.file_path,
     "failed_stage": f.failed_stage, "error_message": f.error_message}
    for f in failing
],
```

- [ ] **Step 4: 跑测试验证 PASS**

Run:
```bash
cd backend && .venv/bin/pytest tests/test_batch_router.py::test_T11_progress_exposes_failed_stage tests/test_batch_router.py::test_T11_3_progress_with_failing_files -v
```
Expected: PASS

- [ ] **Step 5: 跑整体后端套件**(全部测试)

Run:
```bash
cd backend && .venv/bin/pytest tests/ -q
```
Expected: ≥ 173 passed,0 failed(168 基线 + 新增 5 = 173)

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/modules/report/batch_service.py tests/test_batch_router.py
git commit -m "feat(batch): get_progress failing_files 暴露 failed_stage"
```

---

## Task 5: 前端 — `doctorStore` 持久化 role/userId/hospitalId

**Files:**
- Modify: `frontend/packages/doctor-portal/src/stores/doctorStore.ts`

**Interfaces:**
- Produces: 刷新页面后 `role`/`userId`/`hospitalId` 不丢失;`setAuth` 三者写入 localStorage

- [ ] **Step 1: 改 store** — 把整个文件替换为:

```ts
import { create } from 'zustand';
import { createApiClient } from '@hospital/shared';

interface DoctorState {
  token: string | null; userId: number | null; role: string; hospitalId: string | null;
  api: ReturnType<typeof createApiClient>;
  hospitalName: string;
  sidebarCollapsed: boolean;
  setAuth: (token: string, userId: number, role: string, hospitalId: string) => void;
  logout: () => void;
  toggleSidebar: () => void;
}

const getToken = () => localStorage.getItem('doctor_token');
const getRole = () => localStorage.getItem('doctor_role') || '';
const getUserId = () => {
  const v = localStorage.getItem('doctor_user_id');
  return v == null ? null : Number(v);
};
const getHospitalId = () => localStorage.getItem('doctor_hospital_id') || null;

export const useDoctorStore = create<DoctorState>((set) => ({
  token: getToken(),
  userId: getUserId(),
  role: getRole(),
  hospitalId: getHospitalId(),
  api: createApiClient(getToken),
  hospitalName: '',
  sidebarCollapsed: false,
  setAuth: (token, userId, role, hospitalId) => {
    localStorage.setItem('doctor_token', token);
    localStorage.setItem('doctor_role', role);
    localStorage.setItem('doctor_user_id', String(userId));
    localStorage.setItem('doctor_hospital_id', hospitalId);
    set({ token, userId, role, hospitalId });
  },
  logout: () => {
    localStorage.removeItem('doctor_token');
    localStorage.removeItem('doctor_role');
    localStorage.removeItem('doctor_user_id');
    localStorage.removeItem('doctor_hospital_id');
    set({ token: null, userId: null, role: '', hospitalId: null });
  },
  toggleSidebar: () => set(s => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}));
```

- [ ] **Step 2: 跑 tsc + 构建**(若仓库有 typecheck 脚本;否则用 build)

Run:
```bash
cd frontend/packages/doctor-portal && npx tsc --noEmit -p tsconfig.json 2>&1 | head -40
```
(若 `tsc` 不可用,改用 `npm run build` 兜底;无 typecheck 脚本时跳过此步,在 Task 8 整体跑一次)

Expected: 无新增 type error

- [ ] **Step 3: Commit**

```bash
cd frontend && git add packages/doctor-portal/src/stores/doctorStore.ts
git commit -m "feat(doctor-portal): 持久化 role/userId/hospitalId 防刷新丢失"
```

---

## Task 6: 前端 — DoctorLayout 菜单 + RoleGuard + router `/batch` 路由

**Files:**
- Modify: `frontend/packages/doctor-portal/src/components/DoctorLayout.tsx`
- Modify: `frontend/packages/doctor-portal/src/router.tsx`

**Interfaces:**
- Produces: `RoleGuard` 组件(在 router.tsx)可被 Task 7 使用;菜单项条件渲染

- [ ] **Step 1: 改 DoctorLayout 菜单**

在 `frontend/packages/doctor-portal/src/components/DoctorLayout.tsx` 顶部 import `useDoctorStore` 已有;改 `MENU` 列表为可条件渲染。把:

```ts
const MENU = [
  { key: '/', label: '工作台', icon: '📊' },
  ...
  { key: '/dispatch', label: '调度管理', icon: '⚙️' },
];
```

保留静态部分,新增一个 `adminOnly` 项。改为:

```ts
const MENU_BASE = [
  { key: '/', label: '工作台', icon: '📊' },
  { key: '/reports', label: '报告管理', icon: '📋' },
  { key: '/high-risk', label: '高风险人群', icon: '🚨' },
  { key: '/knowledge', label: '知识库管理', icon: '📚' },
  { key: '/triage-rules', label: '三色规则配置', icon: '🎯' },
  { key: '-', label: '—', icon: '' },
  { key: '/statistics/health-profile', label: '健康画像', icon: '📈' },
  { key: '/statistics/cross-compare', label: '多维对比', icon: '🔄' },
  { key: '/statistics/trend', label: '趋势分析', icon: '📉' },
  { key: '/statistics/export', label: '报表导出', icon: '📄' },
  { key: '-', label: '—', icon: '' },
  { key: '/dispatch', label: '调度管理', icon: '⚙️' },
];

const ADMIN_MENU = [
  { key: '/batch', label: '批量上传分发', icon: '📦' },
];
```

在 `DoctorLayout` 函数体内,把现在的:
```ts
const { logout, sidebarCollapsed, toggleSidebar } = useDoctorStore();
```

改为:
```ts
const { logout, sidebarCollapsed, toggleSidebar, role } = useDoctorStore();
const MENU = role === 'admin' ? [...MENU_BASE, ...ADMIN_MENU] : MENU_BASE;
```

后续 `MENU.map` 逻辑不变,自动渲染新项。

- [ ] **Step 2: 在 router.tsx 加 RoleGuard 组件 + /batch 路由**

在 `frontend/packages/doctor-portal/src/router.tsx` 顶部 import 加:

```ts
import BatchUploadPage from './pages/BatchUploadPage';
```

在 `AuthGuard` 函数后新增 `RoleGuard`:

```ts
function RoleGuard({ allow, children }: { allow: string[]; children: React.ReactNode }) {
  const role = useDoctorStore(s => s.role);
  if (!allow.includes(role)) return <Navigate to="/" replace />;
  return <>{children}</>;
}
```

在 `<Routes>` 里加新路由(放在 `dispatch` 路由后):

```tsx
<Route path="/batch" element={
  <AuthGuard>
    <RoleGuard allow={['admin']}>
      <BatchUploadPage />
    </RoleGuard>
  </AuthGuard>
} />
```

- [ ] **Step 3: tsc 检查**

Run:
```bash
cd frontend/packages/doctor-portal && npx tsc --noEmit -p tsconfig.json 2>&1 | head -40
```
Expected: 若报 `'BatchUploadPage' not found` 正常(Task 7 才建);其它 type error 修复。仅在 Task 7 完成后此步才能完全 clean。

- [ ] **Step 4: Commit**

```bash
cd frontend && git add packages/doctor-portal/src/components/DoctorLayout.tsx packages/doctor-portal/src/router.tsx
git commit -m "feat(doctor-portal): 菜单批量上传项 + /batch 路由 + RoleGuard(admin)"
```

---

## Task 7: 前端 — `BatchUploadPage.tsx` 整页

**Files:**
- Create: `frontend/packages/doctor-portal/src/pages/BatchUploadPage.tsx`

**Interfaces:**
- Consumes: `useDoctorStore().api` (axios 实例)、`DoctorLayout`、`/api/v1/reports/batches*` endpoints
- Produces: default export `BatchUploadPage` 组件,接 `null` props

- [ ] **Step 1: 新建页面文件**

Create `frontend/packages/doctor-portal/src/pages/BatchUploadPage.tsx`:

```tsx
import { useState, useRef, useCallback } from 'react';
import { Upload, Button, Progress, message, Tag, Table, Tooltip } from 'antd';
import { InboxOutlined } from '@ant-design/icons';
import { useDoctorStore } from '../stores/doctorStore';
import DoctorLayout from '../components/DoctorLayout';

const CHUNK_SIZE = 5 * 1024 * 1024; // 5MB,与后端 BATCH_CHUNK_SIZE 对齐
const TERMINAL = ['completed', 'partial_failed', 'cancelled'];

const STATUS_COLOR: Record<string, string> = {
  uploading: 'default', extracting: 'blue', parsing: 'gold',
  interpreting: 'orange', completed: 'green', partial_failed: 'red',
  cancelled: 'default',
};

const UNRETRYABLE_STAGES = new Set(['oversize', 'dispatch_unmatched']);

const STAGE_LABEL: Record<string, string> = {
  oversize: '文件过大',
  dispatch_unmatched: '命名不合规',
  parsing: '解析失败',
  interpretation: '解读失败',
};

interface FailingFile {
  id: string;
  file_path: string;
  failed_stage: string | null;
  error_message: string | null;
}

interface BatchProgress {
  id: string;
  filename: string;
  status: string;
  total: number;
  parsed_ok: number;
  interp_ok: number;
  failed: number;
  error_message?: string;
  created_at?: string;
  completed_at?: string;
}

export default function BatchUploadPage() {
  const { api } = useDoctorStore();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'polling'>('idle');
  const [uploaded, setUploaded] = useState(0);
  const [progress, setProgress] = useState<BatchProgress | null>(null);
  const [failing, setFailing] = useState<FailingFile[]>([]);
  const [retrying, setRetrying] = useState(false);
  const batchIdRef = useRef<string | null>(null);

  const poll = useCallback(async (bid: string) => {
    const tick = async () => {
      try {
        const { data } = await api.get(`/reports/batches/${bid}`);
        setProgress(data.batch);
        setFailing(data.failing_files || []);
        if (TERMINAL.includes(data.batch.status)) {
          setPhase('idle'); setBusy(false);
          if (data.batch.status === 'completed') message.success('批量处理完成');
          else if (data.batch.status === 'partial_failed') message.warning('部分文件失败,可在下方查看并重试');
          return;
        }
      } catch { /* 网络抖动,继续 */ }
      timer = window.setTimeout(tick, 5000);
    };
    let timer = window.setTimeout(tick, 3000);
  }, [api]);

  const start = async () => {
    if (!file) return;
    setBusy(true); setPhase('uploading'); setUploaded(0);
    setProgress(null); setFailing([]);
    try {
      // 1. create
      const createForm = new FormData();
      createForm.append('filename', file.name);
      const { data: cd } = await api.post('/reports/batches', createForm);
      const bid = cd.batch_id as string;
      batchIdRef.current = bid;

      // 2. 切片上传(index 0 起)
      const total = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
      for (let i = 0; i < total; i++) {
        const blob = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
        const form = new FormData();
        form.append('index', String(i));
        form.append('total', String(total));
        form.append('data', blob, `${file.name}.part${i}`);
        await api.post(`/reports/batches/${bid}/chunk`, form, {
          headers: { 'Content-Type': 'multipart/form-data' },
          onUploadProgress: (e) =>
            setUploaded(Math.min(file.size, i * CHUNK_SIZE + (e.loaded || 0))),
        });
      }

      // 3. complete(expected_crc32 留空,靠 expected_size 兜底)
      await api.post(`/reports/batches/${bid}/complete`, {
        expected_total: total, expected_size: file.size,
      });

      // 4. 轮询
      setPhase('polling');
      await poll(bid);
    } catch (err: any) {
      const code = err?.response?.data?.detail;
      message.error(code ? `上传失败: ${code}` : '上传失败,请重试');
      setPhase('idle'); setBusy(false);
    }
  };

  const retryAll = async () => {
    const bid = batchIdRef.current;
    if (!bid) return;
    setRetrying(true);
    try {
      const { data } = await api.post(`/reports/batches/${bid}/retry`, {});
      const rq = data.requeued ?? 0;
      const sk = data.skipped_unretryable ?? 0;
      if (rq > 0) {
        message.success(`已重投 ${rq} 个;跳过 ${sk} 个不可重试`);
        setPhase('polling'); setBusy(true); poll(bid);
      } else {
        message.warning(`无可重试文件;跳过 ${sk} 个不可重试`);
      }
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '重试失败');
    } finally {
      setRetrying(false);
    }
  };

  const pct = file ? Math.round((uploaded / file.size) * 100) : 0;
  const done = progress ? (progress.parsed_ok ?? 0) + (progress.interp_ok ?? 0) + (progress.failed ?? 0) : 0;
  const totalFiles = progress?.total ?? 0;

  const failColumns = [
    { title: '文件', dataIndex: 'file_path', key: 'file_path', width: '40%' },
    {
      title: '失败类型', dataIndex: 'failed_stage', key: 'failed_stage', width: 150,
      render: (s: string | null) => (
        <Tag color={UNRETRYABLE_STAGES.has(s || '') ? 'red' : 'orange'}>
          {s ? (STAGE_LABEL[s] || s) : '失败'}
        </Tag>
      ),
    },
    { title: '原因', dataIndex: 'error_message', key: 'error_message' },
  ];

  return (
    <DoctorLayout>
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
        <h2 style={{ marginBottom: 24 }}>📦 批量上传分发</h2>

        {/* 命名约定提示卡 */}
        {!busy && (
          <div style={{
            border: '1px solid var(--color-border)', borderRadius: 8,
            padding: '12px 16px', marginBottom: 16, background: 'var(--color-surface)',
            fontSize: 13, color: 'var(--color-text-secondary)',
          }}>
            <div style={{ fontWeight: 600, color: 'var(--color-text)', marginBottom: 6 }}>
              文件命名要求(必须严格遵循)
            </div>
            <div>每份文件名必须形如:<code>张三_H001_1001.pdf</code> 即 <code>&lt;姓名&gt;_&lt;医院编号&gt;_&lt;用户编号&gt;.ext</code></div>
            <ul style={{ margin: '6px 0 0 20px', padding: 0 }}>
              <li>三段以半角下划线 <code>_</code> 分隔,<b>索引 2(末段)</b>必须是纯数字 user_id</li>
              <li>命名不合规的文件将被标记为 <Tag color="red" style={{ margin: 0 }}>dispatch_unmatched</Tag>,不解析、不可重试</li>
              <li>扩展名仅支持 pdf / doc / jpg / jpeg / png(不含 docx)</li>
              <li>单文件 ≤ 50MB,整包 ≤ 10GB</li>
            </ul>
          </div>
        )}

        {!busy && (
          <div style={{
            border: file ? '2px solid var(--color-primary)' : '2px dashed var(--color-border)',
            borderRadius: 12, padding: '40px 20px', textAlign: 'center',
            background: 'var(--color-surface)',
          }}>
            <Upload.Dragger
              beforeUpload={(f) => { setFile(f); return false; }}
              showUploadList={false}
              accept=".zip,.tar,.gz,.tgz"
              style={{ background: 'transparent', border: 'none' }}
            >
              <InboxOutlined style={{ fontSize: 48, color: 'var(--color-text-secondary)', marginBottom: 16 }} />
              <p style={{ fontWeight: 600 }}>{file ? file.name : '点击或拖拽上传 zip/tar 包'}</p>
              <p style={{ fontSize: 12, color: 'var(--color-text-secondary)' }}>
                包内文件名须符合上述约定
              </p>
            </Upload.Dragger>
          </div>
        )}

        {file && !busy && (
          <Button type="primary" block size="large" onClick={start} disabled={!file}
            style={{ height: 48, marginTop: 24, background: 'var(--color-primary)', border: 'none' }}>
            开始上传
          </Button>
        )}

        {phase === 'uploading' && (
          <div style={{ marginTop: 24 }}>
            <Progress percent={pct} status="active" />
            <p style={{ textAlign: 'center', color: 'var(--color-text-secondary)', marginTop: 8 }}>
              分片上传中 {pct}%
            </p>
          </div>
        )}

        {phase === 'polling' && progress && (
          <div style={{ marginTop: 24 }}>
            <div style={{ marginBottom: 12 }}>
              <Tag color={STATUS_COLOR[progress.status]}>{progress.status}</Tag>
              <span style={{ marginLeft: 12, color: 'var(--color-text-secondary)' }}>
                {done}/{totalFiles} 文件 · 解析 {progress.parsed_ok} · 解读 {progress.interp_ok} · 失败 {progress.failed}
              </span>
            </div>
            <Progress
              percent={totalFiles ? Math.round((done / totalFiles) * 100) : 0}
              status={progress.status === 'partial_failed' ? 'exception' : 'active'}
            />
            <p style={{ fontSize: 12, color: 'var(--color-text-secondary)', marginTop: 8 }}>
              {['parsing', 'interpreting'].includes(progress.status)
                ? '批量任务在夜间 22:00–08:00 时段处理,白天可能停留在此状态,属正常。'
                : '处理中,每 5s 自动刷新…'}
            </p>
          </div>
        )}

        {failing.length > 0 && (
          <div style={{ marginTop: 24 }}>
            <h3 style={{ fontSize: 14, marginBottom: 8 }}>失败文件 ({failing.length})</h3>
            <Table
              dataSource={failing} columns={failColumns} rowKey="id" size="small"
              pagination={false}
              style={{ background: 'var(--color-surface)', borderRadius: 8 }}
            />
            <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end' }}>
              <Tooltip
                title={failing.some(f => UNRETRYABLE_STAGES.has(f.failed_stage || ''))
                  ? '部分文件(naming/size 问题)重试无效,请改文件名后重新上传整批'
                  : '重投所有可重试的失败文件'}
              >
                <Button onClick={retryAll} loading={retrying} disabled={retrying}>
                  重试全部可重试失败文件
                </Button>
              </Tooltip>
            </div>
          </div>
        )}
      </div>
    </DoctorLayout>
  );
}
```

- [ ] **Step 2: tsc 检查**

Run:
```bash
cd frontend/packages/doctor-portal && npx tsc --noEmit -p tsconfig.json 2>&1 | head -60
```
Expected: 无 error。若报 `Module 'antd' has no exported member 'Tooltip'` 等 — 项目 antd 版本若不支持则按报错 adjust。

- [ ] **Step 3: 跑 dev 启动检查页面可加载**(若 dev 脚本存在)

不一定跑构建。可只做 tsc。若选择跑 dev:
```bash
cd frontend && npm run -w packages/doctor-portal dev 2>&1 | head -20 &
# curl localhost:5174/ 看是否 200
```
(若启动口令有别,改用 packages/doctor-portal/package.json 的实际 dev 脚本)

- [ ] **Step 4: Commit**

```bash
cd frontend && git add packages/doctor-portal/src/pages/BatchUploadPage.tsx
git commit -m "feat(doctor-portal): BatchUploadPage 上传+轮询+失败列表+重试双态"
```

---

## Task 8: 文档 — AGENTS.md 补充 `dispatch_unmatched` 失败阶段说明

**Files:**
- Modify: `AGENTS.md`(末尾"新 tenant 初始化必读"节内或新建小节)

- [ ] **Step 1: 在 AGENTS.md 的 `batch_import_file.failed_stage` 说明行后补一行**

把现有:
```
`batch_import_file.failed_stage` 是增量列,旧库需 `ALTER TABLE batch_import_file ADD COLUMN IF NOT EXISTS failed_stage VARCHAR(24) DEFAULT NULL`(`start.sh` 已带,新 tenant 建表时直接包含)。
```

改为追加段:

```
`batch_import_file.failed_stage` 是增量列,旧库需 `ALTER TABLE batch_import_file ADD COLUMN IF NOT EXISTS failed_stage VARCHAR(24) DEFAULT NULL`(`start.sh` 已带,新 tenant 建表时直接包含)。

`failed_stage` 已知取值:`parsing` / `interpretation` / `oversize` / `dispatch_unmatched`。
- `oversize`:单文件 > 50MB,无 `report_task_id`,**不可重试**(UI 禁用重试按钮)。
- `dispatch_unmatched`:批量上传时文件名不符合 `<姓名>_<医院编号>_<用户编号>.<ext>` 约定(三段下划线、末段纯数字),不 create_task 不投 parsing。**不可重试**,需 admin 改文件名后整批重新上传。
- 后端 `retry_failed` 把这两类统称 unretryable,在响应里以 `skipped_unretryable` 计数返回,不重投。
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): 补充 dispatch_unmatched 失败阶段说明"
```

---

## Task 9: 整体验证 — 全套测试 + lint + import sanity

**Files:** 无修改,仅运行验证

- [ ] **Step 1: 后端全套 pytest**

Run:
```bash
cd backend && .venv/bin/pytest tests/ -q
```
Expected: ≥ 173 passed(168 基线 + 至少新增 5 用例)

- [ ] **Step 2: 后端 import sanity**

Run:
```bash
cd backend && .venv/bin/python -c "import app.main; import app.modules.report.extract_worker; import app.modules.report.batch_service; print('imports OK')"
```
Expected: `imports OK`

- [ ] **Step 3: 前端 tsc**

Run:
```bash
cd frontend && npx tsc --build 2>&1 | head -40
```
(若 monorepo 用 `npm run build -ws` 或独立脚本,改用之。)

Expected: 无 type error

- [ ] **Step 4: 把 `npx tsc --build` 做完整一次 clean**

如果出现 error,修后回到 Step 3。直到 0 个 error。

- [ ] **Step 5: 手动验证(可选,部署后)**

按 spec §7.3 清单跑:含 `张三_H001_1001.pdf` 与 `report.pdf` 的小 zip,确认 partial_failed 时 failing_files 表两类失败阶段都正确显示。

- [ ] **Step 6: 终态无需 commit**

(本 task 仅验证,代码已在前序 task 提交)

---

## Self-Review checklist

执行实现前请对照 spec 自检:

- [x] Spec §1 D1-D7 决策都被 Task 1-8 覆盖
- [x] Spec §3.1 `extract_worker` 3 个新增 / 改动点 → Task 1 + Task 2
- [x] Spec §3.2 `retry_failed` unretryable 短路 + 返回值 → Task 3
- [x] Spec §3.3 `get_progress` failing_files 加 failed_stage → Task 4
- [x] Spec §4.1-4.3 前端 4 个文件改动 → Task 5 + Task 6 + Task 7
- [x] Spec §7 测试 5 用例 + 适配现有用例 + 期望 ≥173 passed → Task 1-4
- [x] Spec §9 不动 start.sh / DDL / venv / GPU —— 本计划完全不动这些
- [x] Task 8 文档补充 `dispatch_unmatched` → spec §3.5
- [x] 每个步骤都有具体可运行的命令或具体代码,无 "TBD" / "implement later"
- [x] 类型一致:`_resolve_user_id(filename) -> Optional[int]`、`_record_dispatch_unmatched(db, batch_id, file_path, size, reason)` 在 Task 1/2/3 互相一致;`retry_failed` 返回 `{requeued, skipped_unretryable}` 在 Task 3/7 一致;`get_progress` failing_files 字段 `{id, file_path, failed_stage, error_message}` 在 Task 4/7 一致

注意点:
1. Task 2 Step 8 是测试适配,工作量较大但可批量改;不修会导致现有 T2.1-T2.9 用例大量 fail
2. Task 5 store 改动后需要同步确认 `LoginPage.tsx` 的 `setAuth` 签名仍兼容(原签名 `(token, userId, role, hospitalId)` 一致,无破坏)
3. Task 7 用到 `<Tooltip>` 与 `<Table>` render 函数,若 antd 版本 < 5 需 fallback —— 当前 AGENTS.md 已说明 antd v5
4. Task 8 文档措辞可能与现有段落衔接需要小调整,执行时看上下文定