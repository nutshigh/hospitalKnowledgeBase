# 批量上传报告按身份证后六位分发(外部接口解析医院) 设计

**日期**:2026-09-01
**状态**:Draft(已与用户对齐各节,待 review)
**修订**:2026-09-01 增补「姓名 + 后六位」双锚定(见 §15,最终 review 阶段用户确认)

**前置**:
- 批量上传 + 文件名分发:`docs/superpowers/specs/2026-07-16-batch-dispatch-by-filename-design.md`(已交付)
- 批量上传基础设施:`docs/superpowers/specs/2026-07-15-batch-report-upload-design.md`
- 工程约束:`AGENTS.md`

---

## 0. 目标与边界

### 目标
批量上传的文件命名从 `<姓名>_<医院编号>_<用户编号>.<ext>` 换成 `<姓名>_<身份证后六位>.<ext>`,**用户不再用 user_id 锚定,一切以身份证后六位为主锚定**:

- 后六位(可能以校验位 X 结尾)→ 外部接口解析出用户所在医院的 `hospital_id`
- `report_task.user_id` / `report_info.user_id` **直接改存后六位字符串**(BigInteger → VARCHAR)
- 全链路(报告列表 / user_profile / chat / 统计 / AI agent 层)按后六位过滤
- 登录用户(role='user')的 `platform_user` 新增 `id_card_suffix` 列,登录后带出,用于匹配自己的报告

### 范围内
- 外部接口 resolver 模块(可配置 + 可适配,接口契约后提供)
- `extract_worker` 文件名解析改后六位 + 医院解析 + 批内缓存 + 本地租户校验
- 新失败阶段 `hospital_not_found`(不可重试)
- 表结构:`report_task.user_id` / `report_info.user_id` / `chat_session.user_id` → VARCHAR;`platform_user` 新增 `id_card_suffix`
- 认证与下游全链路适配
- 独立存量迁移脚本
- 对外终端用户注册接口(含 `id_card_suffix`)

### 范围外(YAGNI)
- 存量报告数据回刷(存量不动,只影响新数据)
- 文件名姓名段与外部接口返回姓名的校验(姓名仅展示)
- `hospital_user` 表改造(现为死表,无 Python 引用)
- 前端 user-portal 改动(报告列表走 JWT,无需改前端)

---

## 1. 关键决策记录

| # | 决策 | 选择 | 理由 |
|---|------|------|------|
| D1 | 用户锚定方式 | **身份证后六位** | 用户明确:与 user_id 无关,一切以身份证后六位为主锚定 |
| D2 | 后六位存储位置 | `report_task.user_id` / `report_info.user_id` **列直接改存后六位**(BigInteger → VARCHAR) | 用户明确选择 |
| D3 | 后六位是否含 X | **可能以 X 结尾**(身份证第 18 位校验位可为数字或 X) | 用户确认;正则 `[0-9]{5}[0-9X]` |
| D4 | 外部接口契约 | **待定,按可配置 + 可适配设计** | 接口稍后提供;resolver 模块集中适配 |
| D5 | 接口失败分类 | **无匹配短路,宕机走批次重试** | 无匹配 → `hospital_not_found` 不可重试;宕机 → 现有 extract 批级重试 |
| D6 | 接口调用粒度 | **批内按后六位缓存** | 一个 zip 内同一人多份报告只调一次接口;批间不共享 |
| D7 | 解析出的 hospital_id 本地校验 | **校验本地租户存在性,不存在短路** | 未接入本系统的医院不能落库 |
| D8 | 存量数据 | **存量不动,只影响新数据** | 用户确认 |
| D9 | 下游适配范围 | **全链路都改**(报告列表 / user_profile / chat / 统计 / agent) | 用户确认 |
| D10 | 登录侧后六位来源 | **platform_user 存后六位,登录后带出** | 用户确认 |
| D11 | 方案 | **A:extract_worker 同步解析 + 落库** | 改动集中、复用现有重试/缓存机制 |
| D12 | 存量库迁移 | **独立迁移脚本**(不塞 start.sh) | 用户确认 |
| D13 | 终端用户注册 | **对外提供注册接口**,外部系统调用后写入 platform_user | 用户确认 |

---

## 2. 文件名约定(权威规范)

### 2.1 命名格式
```
<姓名>_<身份证后六位>.<扩展名>
```

- 整体只 1 个文件名(含扩展),路径分隔符无关
- **2 段必须以 `_` 分隔**(strip 出 basename 后,split 函数)
- 各段约束:

| 段索引 | 名称 | 约束 | 后端用途 |
|--------|------|------|---------|
| 0 | 姓名 | 1+ 任意非 `_` 字符(中英文/数字皆可) | 仅展示,不验证 |
| 1 | 身份证后六位 | 5 位纯数字 + 末位 `[0-9X]`(6 字符) | 外部接口解析 → hospital_id;`user_id` 列存此值 |

扩展名只允许:`pdf / doc / jpg / jpeg / png`(沿用现有 `ALLOWED_EXTS`,不含 docx)。此层校验不动。

### 2.2 抽取规则(后端权威)
```python
import re
# 匹配 basename 去扩展名后,2 段 _,末段 = 5 位数字 + 可选 X
_FILENAME_RE = re.compile(r"^([^_]+)_([0-9]{5}[0-9X])$")

def _parse_filename(filename: str) -> Optional[tuple[str, str]]:
    base = os.path.splitext(os.path.basename(filename))[0]
    m = _FILENAME_RE.match(base)
    if m:
        return m.group(1), m.group(2)   # (姓名, 身份证后六位)
    return None
```

### 2.3 示例

| zip 内文件名 | 结果 | 原因 |
|--------------|------|------|
| `张三_123456.pdf` | `id_suffix=123456`,正常 | 2 段,末段 6 位数字 |
| `李四_12345X.pdf` | `id_suffix=12345X`,正常 | 末位 X(校验位) |
| `LiSi_204800.pdf` | 正常 | 姓名段允许英文 |
| `123456.pdf` | None → `dispatch_unmatched` | 只有 1 段 |
| `张三_12345.pdf` | None → `dispatch_unmatched` | 末段只有 5 位 |
| `张三_1234567.pdf` | None → `dispatch_unmatched` | 末段 7 位 |
| `张三_12345Y.pdf` | None → `dispatch_unmatched` | 末位非法字符 |
| `张三_H001_1001.pdf` | None → `dispatch_unmatched` | 旧 3 段格式不再支持 |
| `张三_12345X_extra.pdf` | None → `dispatch_unmatched` | 3 段 |
| `张三_12345X.docx` | 跳过 | docx 不在 `ALLOWED_EXTS`(现状行为) |

### 2.4 大小段说明
50MB 单文件、整包 10GB 限制复用现状。`oversize` → `dispatch_unmatched` → `hospital_not_found` 是三道独立短路,互不替代。检查顺序:大小 → 文件名格式 → 医院解析。

---

## 3. 外部接口 resolver 模块

### 3.1 文件
新增 `backend/app/core/hospital_resolver.py`。

### 3.2 配置(`app/config.py::Settings` 新增,env 可覆盖)
```python
EXTERNAL_RESOLVER_URL: str = ""        # 接口地址;空 = 未配置
EXTERNAL_RESOLVER_TIMEOUT: float = 10.0
```

### 3.3 接口形态
```python
class ResolverUnavailableError(Exception): ...

def resolve_hospital(id_suffix: str) -> Optional[str]:
    """返回 hospital_id(匹配成功)/ None(明确无匹配)。宕机抛 ResolverUnavailableError。"""
```

- 返回 `None`:外部接口明确判定无匹配 → 短路 `hospital_not_found`
- 抛 `ResolverUnavailableError`:超时 / 5xx / 网络错 → 走 extract 批次重试
- `EXTERNAL_RESOLVER_URL == ""` 时:默认返回 `None`(全部短路,行为明确但不可用,防误落库)

### 3.4 请求/响应(暂定最简约定,接口契约后提供时只改此模块内部)
```python
POST {EXTERNAL_RESOLVER_URL}
body: {"id_suffix": "12345X"}
resp: {"hospital_id": "H001"}     # 200 且 hospital_id 非空 → 匹配
                                   # 200 且 hospital_id 空/None → 无匹配
                                   # 4xx(明确 not found)→ 无匹配(容错)
                                   # 5xx / timeout → ResolverUnavailableError
```
请求构造与 `_parse_response` 集中成两个小函数,契约变更时只动这两处。

### 3.5 HTTP 客户端
复用现有 httpx 模式(参考 `backend/app/ai/rag/retriever.py:20`):模块级共享 `httpx.Client(timeout=...)`。

---

## 4. extract_worker 链路改动

### 4.1 正则与解析(`extract_worker.py:24-36`)
见 §2.2。`_parse_filename` 返回值从 `(user_id:int, hospital_code:str)` 改为 `(name:str, id_suffix:str)`。

### 4.2 `_extract_and_enqueue` 内 zip/tar 两分支(`extract_worker.py:114-126 / 145-157`)
替换为:
```python
parsed = _parse_filename(info.filename)
if parsed is None:
    _record_dispatch_unmatched(db, b.id, info.filename, info.file_size)
    continue
name, id_suffix = parsed
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

### 4.3 新增 `_resolve_hospital_id`(批内缓存 + 本地租户校验)
```python
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
```
- `_batch_resolver_cache` 在 `handle_extract_task` 的 finally 里 `pop(batch_id)` 清理。
- `ResolverUnavailableError` 向上抛 → 外层 `handle_extract_task` 现有 `except Exception` 走批次重试(extract_worker.py:57-77),不新增逻辑。

### 4.4 新增 `_hospital_registered`
查 template 库 `hospital_tenant`:
```python
def _hospital_registered(hospital_id: str) -> bool:
    db = next(get_template_db())
    try:
        row = db.execute(
            text("SELECT 1 FROM hospital_tenant WHERE hospital_id = :hid AND is_active = 1"),
            {"hid": hospital_id},
        ).fetchone()
        return row is not None
    finally:
        db.close()
```

### 4.5 `_stream_to_report` 签名(`extract_worker.py:203`)
`user_id: int` → `user_id: str`;传给 `create_task` 的 `user_id=id_suffix`。

### 4.6 新增 `_record_hospital_not_found`
复用 `_record_dispatch_unmatched` 的 uuid 直插模式:
```python
def _record_hospital_not_found(db, batch_id, file_path, size):
    _log.info("extract stage=hospital_not_found batch=%s file=%s size=%d", ...)
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

### 4.7 `create_task` 签名(`report/service.py:16`)
`user_id: int` → `user_id: str`。`ReportInfo(task_id=..., user_id=user_id)` 落库字符串。

---

## 5. 失败阶段语义 + 重试策略

### 5.1 新增 `failed_stage` 值:`hospital_not_found`
- 语义:文件名格式合法(`姓名_后六位`),但外部接口无匹配,或解析出的 hospital_id 本地未注册。
- 与 `dispatch_unmatched`(文件名格式不合法)区分,UI 可分别展示。

### 5.2 `batch_service.py:263` UNRETRYABLE_STAGES 扩展
```python
UNRETRYABLE_STAGES = ("oversize", "dispatch_unmatched", "hospital_not_found")
```
三类均无 report_task_id,`retry_failed` 计入 `skipped_unretryable`,不重投。

### 5.3 外部接口宕机 ≠ 失败阶段
走既有 extract 批级重试(最多 3 次带退避),不产生失败行。

### 5.4 语义矩阵(更新 AGENTS.md)

| failed_stage | 触发 | 重试 |
|---|---|---|
| `oversize` | 单文件 > 50MB | 不可重试 |
| `dispatch_unmatched` | 文件名非 `姓名_后六位` | 不可重试 |
| `hospital_not_found` | 文件名合法但外部接口无匹配 / 本地无此医院 | 不可重试 |
| `parsing` / `interpretation` | 解析/解读失败 | 可重试 |

---

## 6. 表结构变更(DDL)

### 6.1 `report_task` / `report_info` 的 `user_id` 列改字符串
- 现状:`BigInteger NOT NULL`
- 改为:`VARCHAR(16) NOT NULL`(存身份证后六位,如 `12345X`)
- 涉及:
  - `start.sh` 的 `hospital_<id>` DDL 块(新建租户用)
  - `backend/app/modules/report/models.py:9,29` ORM 类型改 `String(16)`

### 6.2 `chat_session.user_id` 改字符串
- `backend/app/modules/chat/models.py:9` `BigInteger` → `String(16)`
- `start.sh` DDL + 存量库增量

### 6.3 `platform_user` 新增 `id_card_suffix`
- `infra/mysql/init/01_template_db.sql`:`ADD COLUMN id_card_suffix VARCHAR(8) NULL`
- 用途:登录后带出后六位,供报告列表 / user_profile / chat 过滤

### 6.4 不变更
- `report_indicator` / `report_interpretation` / `indicator_judgment`:无 user_id,不动
- `batch_import` / `batch_import_file`:`hospital_id` 仍指上传批次所在医院;`failed_stage` 列已有,不加列
- `hospital_user`:死表,不动

### 6.5 独立存量迁移脚本
新增 `backend/scripts/migrate_user_id_suffix.py`(或 `backend/scripts/migrations/` 下),对每个存量 tenant 库:
```sql
ALTER TABLE report_task MODIFY user_id VARCHAR(16) NOT NULL;
ALTER TABLE report_info MODIFY user_id VARCHAR(16) NOT NULL;
ALTER TABLE chat_session MODIFY user_id VARCHAR(16) NOT NULL;
```
以及 template 库:
```sql
ALTER TABLE platform_user ADD COLUMN IF NOT EXISTS id_card_suffix VARCHAR(8) NULL;
```
脚本遍历 `get_all_hospital_ids()` 逐库执行,幂等(重复跑不报错)。

---

## 7. 登录与下游全链路适配

### 7.1 认证链路(`api/auth.py` + `core/dependencies.py`)
- `login` / `register`:`platform_user` 查询/插入加 `id_card_suffix`;放进 JWT claims 与 `TokenResponse`
- `CurrentUser`(dependencies.py:12)加字段 `id_card_suffix: Optional[str]`;`get_current_user` 从 token 解出
- `create_test_user.py` 可选:注册时带 `id_card_suffix`

### 7.2 报告列表(`modules/report/`)
- `router.py:95-96`:`user_id = None if role != "user" else current_user.id_card_suffix`
- `service.py:273` `list_reports(user_id: Optional[str])`;`create_task(user_id: str)`
- 过滤逻辑不变,传值变后六位字符串

### 7.3 user_profile(`modules/user_profile/service.py`)
- `get_overview` 等签名 `user_id: int` → `str`;`router.py:29` 传 `current_user.id_card_suffix`

### 7.4 chat(`modules/chat/`)
- `router.py` / `service.py`:`user_id` 参数 `int` → `str`,传 `current_user.id_card_suffix`
- `chat_session.user_id` 存后六位(列已改 String)

### 7.5 统计(`modules/statistics/`)
- `group_sql.py:153` `ri.user_id AS user_id` 直接输出字符串后六位,无 JOIN 依赖,**不用改 SQL**
- `interpretation/service.py:102` 透传,无需改

### 7.6 AI agent 层(`chat_planner.py` / `tools.py`)
- `user_id` 参数 `Optional[int]` → `Optional[str]`,SQL 参数绑定传字符串后六位

### 7.7 前端 user-portal
- 无需改:登录响应多带 `id_card_suffix` 前端不读;报告列表走 JWT
- (doctor-portal 批量上传页的命名提示文案改为 `姓名_身份证后六位`,属可选同步项)

---

## 8. 对外终端用户注册接口

- **语义**:这是**新增终端用户的完整入口**(不只是一个填充后缀的补丁)——外部系统调用它即创建一个新的 `platform_user` 记录,`id_card_suffix` 是该用户注册信息的一部分。
- **做法**:扩展现有 `api/auth.py::register`(`POST /auth/register`):它当前已负责新增用户(校验 username 唯一 → `INSERT platform_user`),本设计在其请求体上增加 `id_card_suffix` 字段,role='user' 时**必填**,role='doctor'/'admin' 可不填(保持兼容)。
- 外部系统(医院 HIS 等)调用该接口注册新终端用户 → 创建 `platform_user` 行并写入 `id_card_suffix`。
- 校验:`id_card_suffix` 匹配 `^[0-9]{5}[0-9X]$`,且 hospital_id 必填(role='user')。
- 用户明确:外部注册时调用接口再注册到本系统。

---

## 9. 缺失的必需资源清单

**必须由用户提供/确认:**
1. **外部接口契约**(最关键):当前按 §3.4 最简约定设计;接口文档后只需改 resolver 内部两处(请求构造 + `_parse_response`)。接口提供前 `EXTERNAL_RESOLVER_URL` 留空 → 全部 `hospital_not_found`(行为明确但不可用)。
2. **终端用户创建入口**:已定对外注册接口(§8)——外部系统通过它新增带 `id_card_suffix` 的 `platform_user` 用户,新用户注册与后缀来源一体解决。
3. **存量库迁移执行**:已定独立脚本(§6.5),部署时手动跑。

**本设计已自带资源:**
4. 本地租户校验:复用 `hospital_tenant`(`database.py` 已有 `get_all_hospital_ids`)
5. 批内缓存:模块级 dict,随批次清理
6. 接口容错:httpx 超时 + 异常分类,复用现有 extract 重试
7. 测试:resolver 单测(mock httpx)、extract_worker 集成测试(mock resolver)、`hospital_not_found` 重试跳过测试;适配现有 `_parse_filename` 用例

---

## 10. 数据流(端到端,目标态)

```
外部系统 ── POST /auth/register-user(含 id_card_suffix) ──> platform_user.id_card_suffix 写入
admin doctor-portal /batch
  ↓ 选 zip(包内:张三_12345X.pdf,李四_123456.pdf,report.pdf)
  ↓ POST /batches → chunk × N → complete
batch_import.status = extracting
  ↓ extract_worker 解压遍历
对每份文件:
  ├─ 大小 > 50MB → oversize(现状)
  ├─ 文件名非 姓名_后六位 → dispatch_unmatched [新正则]
  ├─ 后六位 → 外部接口(批内缓存)→ hospital_id
  │    ├─ 无匹配 / 本地未注册 → hospital_not_found [新增] 不 create_task
  │    └─ 匹配 → get_hospital_db(hospital_id) → _stream_to_report(user_id=后六位)
  │         create_task(user_id=后六位, hospital_id=外部值) → publish parsing.bulk
  ↓ parsing worker → process_task → report_task.user_id=后六位
  ↓ interpretation worker → AI 解读 → ReportInterpretation 挂在 report_info.user_id=后六位
  ↓
batch_import.status: parsing → interpreting → completed / partial_failed
  ↓
终端用户登录 → JWT 带 id_card_suffix → GET /reports → report_info.user_id == 后六位
  → user-portal 我的报告/档案/chat 全部按后六位匹配
```

---

## 11. 错误与边界

| 场景 | 行为 |
|------|------|
| 外部接口未配置(`EXTERNAL_RESOLVER_URL=""`) | 全部文件 `hospital_not_found`,batch partial_failed |
| 外部接口宕机/超时 | `ResolverUnavailableError` → extract 批级重试(3 次带退避) |
| 外部接口返回无匹配 | 单文件 `hospital_not_found`,不可重试 |
| 外部接口返回的 hospital_id 本地未注册 | 单文件 `hospital_not_found`,不可重试 |
| 同名同后六位多份文件 | 批内缓存命中,只调一次接口,多份正常落库 |
| 存量旧格式文件(`姓名_医院_用户id`) | `dispatch_unmatched`,不可重试(旧格式废弃) |
| admin 整体重试(`POST /retry` 无 file_ids) | 跳过 oversize + dispatch_unmatched + hospital_not_found,返回 `skipped_unretaryable` |
| 存量报告的 user_id 是旧数字 ID | 存量不动;新锚定查不到旧报告(已确认接受) |
| 后六位含非 X 字母 | 正则不匹配 → dispatch_unmatched |

---

## 12. 测试

### 12.1 新增/改动用例

| 文件 | 用例 | 验证点 |
|------|------|--------|
| `test_hospital_resolver.py`(新) | 匹配 / 无匹配 / 宕机(500、超时)/ URL 未配置 | resolver 三类返回值与异常 |
| `test_extract_worker.py` | `test_parse_filename_id_suffix_matches` | 正则匹配 `张三_123456`、`张三_12345X` |
| `test_extract_worker.py` | `test_parse_filename_id_suffix_rejects` | `1001.pdf` / `张三_12345` / `张三_1234567` / `张三_12345Y` / 旧 3 段 |
| `test_extract_worker.py` | `test_T2_12_dispatch_unmatched`(适配) | 非 `姓名_后六位` → dispatch_unmatched |
| `test_extract_worker.py` | `test_extract_creates_task_with_suffix`(适配 T2.13) | mock resolver 返回 H001 → `report_task.user_id == "12345X"` |
| `test_extract_worker.py` | `test_extract_hospital_not_found`(新) | mock resolver 返回 None → failed_stage=hospital_not_found,无 report_task |
| `test_extract_worker.py` | `test_extract_resolver_down_retries_batch`(新) | mock resolver 抛 ResolverUnavailableError → 批次重试路径 |
| `test_batch_service.py` | `test_retry_failed_skips_hospital_not_found`(新/扩展) | retry 跳过 hospital_not_found,返回 skipped_unretaryable |

### 12.2 适配现有用例
- `test_extract_worker.py:303-321` 两个引用 `_resolve_user_id` 的用例(已失效)改为测 `_parse_filename` 或删除
- `test_extract_worker.py` 中 `report_task.user_id == 1001` 断言改为字符串后六位
- `test_batch_service.py:234-266` 现有 unretryable 测试不受影响

### 12.3 回归
`cd backend && .venv/bin/pytest tests/ -q` 全绿。

---

## 13. 风险与回滚

| 风险 | 缓解 |
|------|------|
| 外部接口契约与 §3.4 不符 | resolver 模块集中适配,只改请求构造 + `_parse_response` 两处 |
| 接口宕机导致整批重试 3 次后 partial_failed | 与现有 extract 瞬时异常同语义;可接受 |
| user_id 列类型变更破坏未改到的下游 | 全链路 §7 逐处核对;统计/透传处确认无需改 |
| 存量报告查不到 | 用户已确认存量不动,只影响新数据 |
| 后端基线回归 | 独立 resolver + mock,跑全量 pytest |

回滚:改动集中在 extract_worker / batch_service / report service / models / auth / dependencies / 新增 resolver 模块。`git revert` 单提交即可;DDL 变更独立迁移脚本,可逆(列改回 BigInteger 需确认存量字符串值)。

---

## 14. 落地工作分解(给 writing-plans 的输入)

1. **后端** 新增 `app/core/hospital_resolver.py` + config 两项
2. **后端** `extract_worker.py`:正则 / `_parse_filename` / `_resolve_hospital_id` / `_hospital_registered` / `_record_hospital_not_found` / `_stream_to_report` 签名 / 两分支调用点 / 缓存清理
3. **后端** `batch_service.py` retry_failed UNRETRYABLE_STAGES 加 `hospital_not_found`
4. **后端** `report/service.py` create_task user_id → str;`report/models.py` / `chat/models.py` ORM 类型
5. **后端** auth:login/register 带 id_card_suffix;`dependencies.py` CurrentUser 加字段;`register` 加后六位校验
6. **后端** 下游:report router/service、user_profile、chat、chat_planner、tools 的 user_id 类型适配
7. **DDL** `start.sh` 新建租户 DDL 块改三表 + `01_template_db.sql` 加列;独立迁移脚本 `backend/scripts/...`
8. **测试** §12 全部用例 + 全量回归
9. **文档** `AGENTS.md` 更新命名约定 / failed_stage 矩阵 / 后六位锚定说明;旧 spec 标注废弃
10. **前端(可选)** doctor-portal 批量上传页命名提示文案
11. **commit 策略** 后端主改动一 commit(tests green)→ DDL/迁移一 commit → 文档一 commit

---

## 15. 增补:姓名 + 后六位双锚定(2026-09-01 最终 review 阶段确认)

### 15.1 动机
最终 review 指出:仅凭身份证后六位(≈1.1M 组合)做唯一身份锚定,存在两人撞后缀 → 共享全部报告/档案/chat 的风险。用户确认:**锚定改为「姓名 + 身份证后六位」的组合,与报告文件名 `<姓名>_<后六位>` 一致**。

### 15.2 变更点

1. **DDL**:
   - `platform_user` 新增 `name VARCHAR(50)` 列(登录姓名,注册时由外部系统传入)
   - `chat_session` 新增 `name VARCHAR(50)` 列(chat 会话双锚定)
   - `01_template_db.sql` / `003_user_id_suffix.sql` / `start.sh` else-branch 同步
2. **register**:请求体加 `name`(role='user' 必填);唯一性校验 `(hospital_id, name, id_card_suffix)` 已存在则拒绝;INSERT 带 name
3. **login**:SELECT name → JWT claim → 响应带 name;`CurrentUser` 加 `name` 字段
4. **报告匹配双条件**:报告列表 / user_profile / chat 过滤 `user_id == 后六位 AND name == 登录姓名`
5. **批量上传**:`_stream_to_report` / `create_task` 接收文件名姓名段 → 写入 `report_info.name`;`process_task` 仅在 name 为空时用 VLM 解析填充
6. **单独上传**:保留 VLM 解析逻辑(仅在 name 为空时填充;已存则用登录姓名)
7. **测试**:register 唯一性、JWT 带 name、报告双条件过滤、批量落库文件名姓名

### 15.3 与既有实现的关系
既有 Task 1-10(后六位锚定 + 医院解析 + 字符串化)全部保留;本增补在其上叠加姓名维度,不改变后六位的存储与解析链路。
