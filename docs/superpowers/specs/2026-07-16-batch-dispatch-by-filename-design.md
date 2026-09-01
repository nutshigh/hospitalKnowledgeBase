# 批量上传报告分发到用户(按文件名约定) 设计

> **已废弃(2026-09-01)**:命名约定改为 `<姓名>_<身份证后六位>`,见 `2026-09-01-batch-upload-idcard-suffix-design.md`。

**日期**:2026-07-16
**状态**:Draft（已与用户对齐方向,待 review）
**前置**:
- 后端批量上传基础设施:`docs/superpowers/specs/2026-07-15-batch-report-upload-design.md`(已交付 18 commit,168 tests)
- 前端接入指导:`docs/batch-upload-frontend-guide.md`(已给上传+轮询骨架)
- 工程约束:`AGENTS.md`(venv / GPU / 多租户)

---

## 0. 目标与边界

### 目标
让医院管理员(role=admin 且 hospital_id 非空)把一批纸质/电子体检报告一次性 zip 上传:
- 后端解压、按 **文件名约定** 抽取每个文件归属的 `user_id`,
- 每份 `report_task` 的 `user_id` **写正确的真实终端用户**而非上传者,
- 走现有 parsing → interp 流水,产出报告挂到正确用户名下,
- admin 在轮询 UI 看到「文件名/大小不合规」的失败项,标红且**重试按钮禁用**(因为重试也无效)。

### 范围内
- 文件名约定解析后端逻辑 + 新失败阶段 `dispatch_unmatched`
- 前端 doctor-portal `/batch` 页面(上传 + 轮询 + failing_files 表 + 不可重试失效态)
- 后端 `retry_failed` 排除 `oversize` / `dispatch_unmatched`
- `get_progress` / `list_batches` 响应之一致带 `failed_stage`

### 范围外(故意不做,YAGNI)
- 清单 CSV 预览映射(已选否,见决策记录 §1)
- `hospital_user` DB 校验 user_id 真实存在(mock 无数据;真实生产可作 future)
- 文件名里 `<医院编号>` 段回校 JWT hospital_id
- 用 VLM 提取的中文姓名做自动 user 匹配套索
- admin-portal 改造
- `start.sh` / GPU / worker 进程数

---

## 1. 关键决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| D1 | 文件 → user_id 映射机制 | **C 文件名约定自动映射** | 适合批量、零额外清单、零后处理 UI;医院 HIS 导出文件命名可约束 |
| D2 | 文件名携带 user_id 的拼写规则 | `<姓名>_<医院编号>_<用户编号>.<ext>`,3 段 `_` 分隔,索引 2 = user_id | 用户指定。数字段便于正则,姓名段允许中英文 |
| D3 | 抽不出 user_id 的 fallback | **A** `failed_stage='dispatch_unmatched'` 不解析 | 与现有 oversize 同等级短路,UI 可区分且禁用重试 |
| D4 | 是否在上传后做 dry-run 预览映射 | **A** 不预览,直接进 extract | 实现最简,unmatched 在轮询结果里可见即可 |
| D5 | 文件名里医院编号段是否回校 JWT | **A** 忽略不校 | tenant 隔离已由 JWT + `_db` 保证 |
| D6 | 放在哪个 portal | **doctor-portal** | 现有后端守门强校 `hospital_id` + `role==='admin'`;admin-portal 是平台运营无 hospital_id,走不通 |
| D7 | 不命中 user_id 的文件是否仍创建 report_task | 否,不 create_task | 不污染解析流水;admin 改名后重新上传新批次即可 |

---

## 2. 文件名约定(权威规范)

### 2.1 命名格式
```
<姓名>_<医院编号>_<用户编号>.<扩展名>
```

- 整体只 1 个文件名(含扩展),路径分隔符无关
- **3 段必须以 `_` 分隔**(strip 出 basename 后,split 函数)
- 各段约束:

| 段索引 | 名称 | 约束 | 后端用途 |
|--------|------|------|---------|
| 0 | 姓名 | 1+ 任意非 `_` 字符(中英文/数字皆可) | 仅展示,不验证 |
| 1 | 医院编号 | 1+ 任意非 `_` 字符 | 忽略(D5) |
| 2 | 用户编号 | 1+ 纯十进制数字(≥1) | `user_id = int(段2)` |

扩展名只允许:`pdf / doc / jpg / jpeg / png`(沿用现有 `ALLOWED_EXTS`,**不含 docx**)。这一层校验已在 `_extract_and_enqueue` 行内做,本设计不动。

### 2.2 抽取规则(后端权威)

```python
import re
# 匹配 basename 去扩展名后,3 段 _,末段纯数字
_FILENAME_RE = re.compile(r"^([^_]+)_([^_]+)_(\d+)$")

def _resolve_user_id(filename: str) -> Optional[int]:
    """从 zip/tar 内文件名抽取目标 user_id。
    Returns int user_id on match, None on mismatch.
    """
    base = os.path.splitext(os.path.basename(filename))[0]
    m = _FILENAME_RE.match(base)
    if not m:
        return None
    return int(m.group(3))
```

### 2.3 示例

| zip 内文件名 | 结果 | 原因 |
|--------------|------|------|
| `张三_H001_1001.pdf` | `user_id=1001`,正常 | 3 段,末段数字 |
| `LiSi_H002_2048.pdf` | `user_id=2048`,正常 | 姓名段允许英文 |
| `1001.pdf` | None → `dispatch_unmatched` | 只有 1 段 |
| `张三_H001_abc.pdf` | None → `dispatch_unmatched` | 末段非数字 |
| `张三_1001.pdf` | None → `dispatch_unmatched` | 只有 2 段 |
| `张三_H001_1001_extra.pdf` | None → `dispatch_unmatched` | 4 段 |
| `MACOSX/._张三_H001_1001.pdf` | 跳过 | 现有 `__MACOSX` / 点开头过滤 |
| `张三_H001_1001.docx` | 跳过 | docx 在 `ALLOWED_EXTS` 之外(现状行为) |

### 2.4 大小段说明

50MB 单文件限制、整包 10GB 限制仍复用现状;**`dispatch_unmatched` 与 `oversize` 是独立的两道短路**,互不替代。一个文件可能 oversize(优先记 oversize)或 dispatch_unmatched(命名问题),但不会同时。后端先检大小(现状)再检 user_id 抽取。

---

## 3. 后端改动

### 3.1 `extract_worker.py` 改动

**新增**:
```python
_FILENAME_RE = re.compile(r"^([^_]+)_([^_]+)_(\d+)$")

def _resolve_user_id(filename: str) -> Optional[int]:
    base = os.path.splitext(os.path.basename(filename))[0]
    m = _FILENAME_RE.match(base)
    return int(m.group(3)) if m else None

def _record_dispatch_unmatched(db, batch_id, file_path, size, reason="dispatch_unmatched"):
    """记一行 file failed,但既不落盘也不投 parsing(与 oversize 同一短路等级)。

    注意:不能复用 `BatchService.handle_extracted_file` 的 `(batch_id,crc32)` 去重,
    因为占位 crc 会把两个同 size 的 unmatched 文件误判为同一行被吞。
    这里直接新建一行 `BatchImportFile`(uuid 主键自动生成,占位唯一 crc 不参与去重)。
    """
    import uuid
    fid = uuid.uuid4().hex
    db.add(BatchImportFile(
        id=fid, batch_id=batch_id, file_path=file_path,
        file_size=size, crc32=f"unm{uuid.uuid4().hex[:8]}",
        status="failed", failed_stage="dispatch_unmatched", error_message=reason,
    ))
    b = db.query(BatchImport).get(batch_id)
    if b is not None:
        b.failed = (b.failed or 0) + 1
    db.commit()
```

(注:`_record_oversize` 现有代码也有同 size 文件被去重吞掉的类似问题,但超出本 spec 范围不修;dispatch_unmatched 走独立路径规避。)

**改 `_extract_and_enqueue` 顺序**:在 oversize 检查之后、`_stream_to_report` 之前插入 user_id 解析:
```python
# (现状)if info.file_size > settings.BATCH_FILE_MAX_SIZE: _record_oversize(...); continue
# (现状)if cum_uncompressed > 5 * archive_size: _record_oversize(...); continue

# 新增:user_id 解析短路
user_id = _resolve_user_id(info.filename)
if user_id is None:
    _record_dispatch_unmatched(db, b.id, info.filename, info.file_size)
    continue

with zf.open(info) as fh:
    _stream_to_report(db, b, hospital_id, info.filename, fh, info.file_size, user_id)
```

tar 分支同此插入。

**改 `_stream_to_report` 签名**:新增 `user_id: int` 形参。原 `user_id=int(b.user_id) if str(b.user_id).isdigit() else 0` 这行删除,改用传入参数:

```python
def _stream_to_report(db, b, hospital_id, rel_path, fh, size, user_id: int):
    ...
    task = create_task(
        db=db, hospital_id=hospital_id,
        user_id=user_id,   # ← 不再是 b.user_id
        file_path=disk_path, filename=os.path.basename(rel_path),
        file_type=file_type, file_size=size, priority="bulk",
        batch_id=b.id, file_id=fid,
    )
```

### 3.2 `batch_service.py` retry_failed 改动

`retry_failed` 在迭代 failed files 时,**跳过 `failed_stage ∈ {'oversize','dispatch_unmatched'}`**——这两类没有 `report_task_id`,重试无意义。返回值新增一个字段:

```python
return {"requeued": requeued, "skipped_unretryable": skipped_unretryable}
```

变更点:
| 项 | 改动 |
|----|------|
| 过滤逻辑 | 加入 `if f.failed_stage in ("oversize", "dispatch_unmatched"): skipped_unretryable += 1; continue` |
| 状态推进 | `b.failed` 减少 = `requeued`(不含 skipped);若 requeued == 0 不动 `b.status` |
| 测试新增 | `test_retry_failed_skips_dispatch_unmatched_and_oversize` |

### 3.3 `batch_service.py:get_progress` 改动

`failing_files` 项多带 `failed_stage`(只读字段):

```python
"failing_files": [
    {"id": f.id, "file_path": f.file_path, "failed_stage": f.failed_stage,
     "error_message": f.error_message}
    for f in failing
]
```

(`failed_stage` 列在 Schema 已存在。)

### 3.4 `batch_router.py` 无需改

`POST /batches/{id}/retry` 响应原本是 `{"requeued": n}`,现在多带 `skipped_unretryable`。前端兼容(只读 `requeued`)。

### 3.5 `start.sh` / DDL / Schema 无需改

`failed_stage` 列已存在;新语义只在 worker 里发值。AGENTS.md 顺手补一行说明 `dispatch_unmatched` 与 oversize 同语义。

---

## 4. 前端改动(doctor-portal)

### 4.1 文件清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `src/pages/BatchUploadPage.tsx` | **新建** | 见 §4.2 详细 UI |
| `src/router.tsx` | 改 | 加 `<Route path="/batch" element={<AuthGuard><RoleGuard allow={['admin']}><BatchUploadPage/></RoleGuard></AuthGuard>} />` |
| `src/components/DoctorLayout.tsx` | 改 | `MENU` 加 `{ key:'/batch', label:'📦 批量上传分发' }`,**仅 `role==='admin'` 渲染该项**;`RoleGuard` 在同文件或 `router.tsx` 内实现(无第三人库依赖) |
| `src/stores/doctorStore.ts` | 改 | 持久化 `role`/`userId`/`hospitalId` 到 localStorage(刷新不丢菜单守卫);与 `docs/batch-upload-frontend-guide.md §1` 一致 |

### 4.2 `BatchUploadPage.tsx` UI 形态

沿用 guide §6.1 骨架(antd v5 + 内联 CSS 变量):

#### 4.2.1 上传前(选 zip 阶段)
```
[拖拽上传 zip/tar]
─────
📦 批量上传分发

⚠️ 文件命名要求:
每份文件名必须形如 <姓名>_<医院编号>_<用户编号>.<扩展名>
   示例:张三_H001_1001.pdf
   命名不符合的文件将被标记为「dispatch_unmatched」不解析。
   扩展名仅支持 pdf/doc/jpg/jpeg/png(不含 docx)
   单文件 ≤ 50MB,整包 ≤ 10GB

[开始上传] (禁用直到选中文件)
```

#### 4.2.2 上传中
分片进度条 + 状态:`分片上传中 NN%`

#### 4.2.3 轮询中
- `Tag` 显示 batch 状态(`extracting/parsing/interpreting`)颜色复用 guide 里的 `STATUS_COLOR`
- `parsed_ok / interp_ok / failed / total` 汇总
- 「批量在夜间 22:00-08:00 处理」提示(沿现状)
- `partial_failed` 时揭示 `failing_files` 表

#### 4.2.4 `failing_files` 表
列:

| 列 | 渲染 |
|----|------|
| `file_path` | 文本 |
| `failed_stage` | `<Tag color="red">`:oversize / dispatch_unmatched / parsing / interpretation / null → 显示「解析失败」 |
| `error_message` | 文本 |
| 操作 | 失败阶段 ∈ `{oversize,.dispatch_unmatched}` → `<Button disabled>重试</Button>` + Tooltip「文件名/大小不合规,重试无效,请改后重新上传整批」<br>其它 → `<Button onClick={retry}>重试</Button>`(可选勾选 file_ids) |

底部按钮:
- `重试全部可重试失败文件` —— 调 `POST /retry`(不带 file_ids = 全部 failed),后端会自己跳过 unretryable,响应 `{requeued, skipped_unretryable}`,前端 toast 显示「重投 X 个、跳过 Y 个不可重试」。

### 4.3 复用 helper(可选)

`packages/shared/src/api/batch.ts`(guide §6.2 已给骨架)。本设计**不强求**抽 helper,留在 page 内即可;若 page > 300 行则抽,否则不抽,保持单一 page 文件清晰。

---

## 5. 数据流(端到端,目标态)

```
admin doctor-portal /batch
  ↓ 选 zip(包内:张三_H001_1001.pdf,李四_H001_1002.pdf,report.pdf)
  ↓ POST /batches → POST /chunk × N → POST /complete
batch_import.status = extracting
  ↓ extract_worker 解压遍历文件
对每份文件:
  ├─ 大小 > 50MB → _record_oversize() (现状)
  ├─ 大小 OK 但 _resolve_user_id(filename) is None → _record_dispatch_unmatched()  [新增]
  │    file.failed_stage='dispatch_unmatched', error_message='dispatch_unmatched'
  │    不 create_task,不投 parsing
  ├─ user_id 命中 → _stream_to_report(.., user_id=1001)
  │    create_task(user_id=1001, batch_id=..., file_id=fid) → publish parsing.bulk
  ↓
parsing worker → process_task → OCR/VLM parse → report_task.user_id=1001
  ↓ publish interpretation.bulk
interpretation worker → AI 解读 → ReportInterpretation 挂在 report_info.user_id=1001
  ↓
batch_import.status: parsing → interpreting → completed(全成功) / partial_failed
  ↓
admin 页面轮询看到 partial_failed,展开 failing_files 表:
  - report.pdf / dispatch_unmatched / dispatch_unmatched / [重试] disabled
  ↓
user_id=1001 在 user-portal 我的报告列表看到新报告(原有路径,无需改 user-portal)
```

---

## 6. 错误与边界

| 场景 | 行为 |
|------|------|
| admin 上传一个全空 zip | `partial_failed / no_valid_files`(现状) |
| zip 内全是 `1001.pdf` 命名 | 100% `dispatch_unmatched` → partial_failed;UI 表全标红 |
| zip 内混有合规与不合规 | 合规走流水,不合规 `dispatch_unmatched`;最终 partial_failed |
| user_id 在 `hospital_user` 不存在 | 仍然 create_task(D2 范围外:不查 user DB)。挂在不存在的 user_id 名下,user 端看不到。**Future:** 加 user 存在性校验 |
| admin 在 partial_failed 后整体重试(`POST /retry` 无 file_ids) | 后端跳过 oversize+dispatch_unmatched,返回 `skipped_unretryable` |
| admin 改完文件名想补救 | 重新上传一个**新**批次。当前批次已记账的 dispatch_unmatched 行不可反转 |
| oversize 文件被重命名为合规 | 仍 oversize(大小检查在前) |
| 命名带空格 / 全角下划线 `__` | split 不命中 → dispatch_unmatched。文档需明示用半角 `_` |

---

## 7. 测试

### 7.1 后端(`backend/tests/`,用 pytest)

新增/改动共 **5** 个用例(从基线 168 → 期望 173 passing):

| 文件 | 用例 | 验证点 |
|------|------|--------|
| `test_extract_worker.py` | `test_resolve_user_id_matches_three_segment` | 正则单元 |
| `test_extract_worker.py` | `test_resolve_user_id_rejects_non_numeric_or_missing_segment` | 反例覆盖 1001.pdf / 张三_H001_abc.pdf / 张三_1001.pdf / 4 段 |
| `test_extract_worker.py` | `test_extract_creates_task_with_filename_user_id` | 集成:`张三_H001_1001.pdf` 走 mock-extract → `report_task.user_id == 1001` |
| `test_extract_worker.py` | `test_extract_marks_dispatch_unmatched_when_name_invalid` | `report.pdf` → file.failed_stage='dispatch_unmatched',无 report_task |
| `test_batch_service.py` | `test_retry_failed_skips_dispatch_unmatched_and_oversize` | retry 不路由到这两类,返回 `skipped_unretryable` 计数 |

### 7.2 适配现有用例
- 现有 `test_extract_worker.py` 中如有断言 `report_task.user_id == int(b.user_id)` 的,改为 `== 1001`(或对应 mock 命名)。如有内容及文件名混用案例 → 已实现可能需更新。
- `test_batch_service.py: test_retry_failed_interp_stage_routes_to_interp_bulk` 不受影响(它不是 unretryable)。

### 7.3 前端
本工程前端无单测框架(确认中)。**手动验证清单**(部署后再跑):
1. 上传一个含 `张三_H001_1001.pdf` 与 `李四_H001_1002.pdf` 的小 zip → 最终 completed,`interp_ok=2`,user 1001/1002 在自己的报告列表看到这两份。
2. 上传含 `report.pdf`(不含 `_`)的 zip → partial_failed,该行 failed_stage=dispatch_unmatched,重试按钮禁用。
3. 上传含 `张三_H001_1001.docx` 的 zip → 该文件被现状静默跳过(不在 failing_files 里出现,因为 extract_worker 在 ext 过滤阶段就 continue 了)。
4. 上传含 1 合规 + 1 oversize(假 50MB+ PDF) + 1 dispatch_unmatched 的 zip → partial_failed,failing_files 表三行分别显示三种 failed_stage,重试按钮按 unretryable 列表禁用对应两个。
5. 后端基线不回归:`cd backend && .venv/bin/pytest tests/ -q` ≥ 168 passed。

---

## 8. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 现有调用 `_stream_to_report` 不传 user_id 的旧路径 | 仅 `extract_worker._extract_and_enqueue` 是唯一调用,改动同提交内闭合 |
| 文件名取错 user_id 挂错人 | UI 上提示卡明示规则;`dispatch_unmatched` 失败不静默;future 加 user DB 校验 |
| `failed_stage` 语义扩展破坏既有 retry 路径 | retry 仅新增「排除两类 unretryable」分支,不改既有 parsing/interpretation 路由 |
| 共享代码 `int(b.user_id) if str(b.user_id).isdigit() else 0` 被删 | 此行只服务于旧 batch 路径,现已统一从文件名取——但需确认无别处调用签名依赖 |

回滚:本改动凝在 3 个文件(extract_worker/batch_service/batch_router-failing 集合),`git revert` 单提交即可;`failed_stage` 列无需 DDL 变更。

---

## 9. 部署影响

无:不改 `start.sh`,不改 venv / vLLM / GPU 配置,不改 RabbitMQ 队列,不改 DDL,不改 worker 进程数。
- `failed_stage='dispatch_unmatched'` 仅是新写入的字符串值,DB 列已是 `VARCHAR(24)`。
- 前端只新增页面,不动既有路由(`/reports` 等)。

---

## 10. 落地工作分解(给 writing-plans 的输入)

1. **后端** `_resolve_user_id` + `_record_dispatch_unmatched` + `_stream_to_report` 签名调整 + 调用点更新(extract_worker.py)
2. **后端** `batch_service.py` retry_failed 加 unretryable 短路 + 返回值扩展
3. **后端** `get_progress` failing_files 项加 `failed_stage`
4. **后端** 5 个 pytest 用例 + 适配既有用例 + 跑 `pytest tests/ -q` 期望 ≥ 173 passing
5. **前端** `doctorStore` 持久化 role/userId/hospitalId
6. **前端** `DoctorLayout` 菜单 + `RoleGuard` + `router.tsx` 路由
7. **前端** `BatchUploadPage.tsx` 整页(上传提示卡 + 切片上传 + 轮询 + failing_files 表 + 重试按钮双态)
8. **前端** 手动验证清单跑通
9. **文档** `AGENTS.md` 末尾补一行 `dispatch_unmatched` 失败阶段说明
10. **commit 策略** 后端先一 commit(tests green);前端一 commit;docs 一 commit