# 新增 Tenant 接口 — 设计

> Date: 2026-07-17
> Status: Approved (design)
> Owner: AI Agent

## 1. 背景与动机

当前架构下,新增医院租户必须手工执行三步:
1. 在模板库 `hospital_template.hospital_tenant` 表登记一条记录;
2. `CREATE DATABASE hospital_<hospital_id>`;
3. 把 `start.sh:108-124` 的 17 张表的 `CREATE TABLE IF NOT EXISTS ...` DDL 整段复制到新库上跑一遍(尤其别忘了 `batch_import`/`batch_import_file` 和 `failed_stage` 列)。

漏掉任何一步,前端上传到该租户的报告会在对应阶段直接以 SQL 错误(1049/1146/1054)形式抛出 —— 既不会自动建库,也不会自动建表(已核实:`backend/app/core/database.py:49-55` 的 `get_hospital_db` 只拼接库名开 session,没有任何 provision 逻辑;Python 代码 0 处 `CREATE TABLE`)。

本设计提供一个 HTTP 接口把上述三步合一,降低运维门槛和漏建表的概率。

## 2. 现状关键事实

- 模板库表:`hospital_tenant(hospital_id, hospital_name, db_name, is_active)`、`platform_user(...)` —— 定义见 `infra/mysql/init/01_template_db.sql`。
- 已有存储过程 `infra/mysql/init/02_hospital_created.sql`:`create_hospital_database(p_hospital_id)` 按 `hospital_<id>` 建库 + 建表。
- **该存储过程严重落后于 `start.sh:108-124` 的最新 DDL**:
  - 缺 `chat_session`、`chat_message`、`batch_import`、`batch_import_file` 四张表;
  - `report_interpretation` 缺 `summary_refs`/`comparison_summary`/`comparison_baseline_id`/`quality_note` 四列(manual migration 001/002 新增的);
  - `indicator_judgment` 已对齐 `certainty`/`certainty_reason`,这一张 OK;
  - `batch_import_file.failed_stage` 不存在。
- Python 模块路由模式规整:`app/modules/<module>/{router,service,schemas,__init__}.py`,已有 `require_role` 依赖可限制角色。
- `backend/app/utils/exceptions.py` 已有 `AppException`/`ValidationException`/`NotFoundException`/`UnauthorizedException`/`ForbiddenException`,缺 409 `ConflictException`(本设计暂不引入冲突语义,见 §6)。

## 3. 决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 鉴权 | 头部 `X-Admin-Token` 校验 `settings.ADMIN_TOKEN`;空字符串放行 | 用户要求"无鉴权或简单 token";留空即可完全开放,填值即变成共享密钥 |
| DDL 来源 | 复用并升级存储过程 `create_hospital_database`,Python 只 `CALL` | SQL 集中在 MySQL,DDL 维护不散落到 Python,符合 §brainstorming 选项 |
| 接口范围 | MVP 仅 `POST /api/v1/tenants` 一个端点 | 用户要求"仅 POST 创建";后续 GET/PATCH 再加 |
| 重复处理 | 幂等:已在 `hospital_tenant` 表登记 → 直接返回原记录(200),不报错不重跑 CALL | 用户要求"幂等";安全前提是存储过程表全用 `CREATE TABLE IF NOT EXISTS` |
| 模块位置 | `app/modules/tenant/`(router/service/schemas/__init__) | 与现有 7 个业务模块结构一致,未来加列表/启停接口无需重构 |
| 孤儿库 | CALL 成功但 INSERT 失败 → rollback INSERT,记 WARN,不删库 | DDL 无法回滚,自动删库有误删风险;下次重试同 hospital_id 时 `IF NOT EXISTS` 兜底 |
| Milvus | 不在创建时初始化命名空间 | 现状 `ensure_milvus_started` 已延迟到首次使用时建 namespace,不打破现有行为 |

## 4. 数据流

```
Client ──POST /api/v1/tenants──▶ router
  router ──validate body──▶ service.create_tenant(req)
    service ──SELECT hospital_tenant WHERE hospital_id=?
      ├─ row exists  ─▶ return {created:false, ...existing}      (200)
      └─ row absent  ─▶ CALL create_hospital_database(:hid)       (DDL 不可回滚)
                       └─ on error:  rollback, log, raise 500
                       INSERT INTO hospital_tenant ...
                       └─ on error:  rollback INSERT, log WARN, raise 500
                                     (孤儿库留下;下次同 id 重试时 IF NOT EXISTS 兜底)
                       commit
                       return {created:true, hospital_id, db_name, is_active:1}
```

## 5. API 契约

### 请求

```
POST /api/v1/tenants
Header:
  X-Admin-Token: <optional shared secret>;  仅当 settings.ADMIN_TOKEN 非空时校验
Body (application/json):
{
  "hospital_id":   "H002",   # 必填, ^[A-Za-z0-9]{2,16}$, 不含下划线(避免破坏 hospital_<id> 命名约定)
  "hospital_name": "示例医院"  # 必填, 非空, 1..100 字符
}
```

### 响应

成功(200,无论首次还是已存在):

```json
{
  "created":      true,            // 首次创建为 true,已存在为 false
  "hospital_id":  "H002",
  "db_name":       "hospital_H002",
  "hospital_name": "示例医院",
  "is_active":     1
}
```

### 错误

| 状态码 | code | 何时触发 |
|---|---|---|
| 400 | VALIDATION_ERROR | `hospital_id` 格式不合法 / `hospital_name` 空或超 100 |
| 401 | UNAUTHORIZED | `settings.ADMIN_TOKEN` 非空且请求头不匹配 |
| 500 | INTERNAL_ERROR | CALL 存储过程失败,或 INSERT hospital_tenant 失败 |

不引入 409 冲突:幂等语义下重复调用即返回旧记录,不算冲突。

## 6. 存储过程升级(`infra/mysql/init/02_hospital_created.sql`)

把存储过程整段重写,使其 DDL 与 `start.sh:108-124` 完全对齐,并合并 `manual_migrations/001`、`002` 的增量列。新 tenant 一建出来即等于 `hospital_H001` 当前态,**无需再跑迁移**。

表清单(全部 `CREATE TABLE IF NOT EXISTS`,幂等):
- `hospital_user`
- `knowledge_category`
- `knowledge_entry`
- `report_task`
- `report_info`
- `report_indicator`
- `report_interpretation` —— 含 `summary_refs JSON`、`quality_note VARCHAR(255)`、`comparison_summary TEXT`、`comparison_baseline_id BIGINT`
- `indicator_judgment` —— 含 `certainty VARCHAR(10)`、`certainty_reason TEXT`
- `triage_rule`
- `report_template`
- `statistic_cache`
- `dispatch_config`
- `resource_metric`
- `chat_session`
- `chat_message`(含 `FOREIGN KEY (session_id) REFERENCES chat_session(id)`)
- `batch_import`(含索引 `idx_batch_status`/`idx_batch_hospital`)
- `batch_import_file`(含 `failed_stage VARCHAR(24) DEFAULT NULL`、`UNIQUE KEY uq_batch_file (batch_id, crc32)`、外键到 `batch_import(id)`)

字符集统一 `utf8mb4 DEFAULT CHARSET=utf8mb4`。

`start.sh:108-124` 那段保留不动(它仍管 `hospital_H001` 的首次启动),但两处的 DDL 字面量必须保持一致(后续可考虑抽 `infra/mysql/tenant_schema.sql` 让两边共用文件,但本期不做这个重构,见 §10)。

## 7. Python 模块

### 7.1 文件

```
backend/app/modules/tenant/
├── __init__.py
├── router.py        # POST /api/v1/tenants
├── service.py       # 幂等 + CALL + INSERT
└── schemas.py       # pydantic 模型
```

`backend/app/main.py` 注册: `app.include_router(tenant_router, prefix="/api/v1/tenants", tags=["tenant"])`

### 7.2 schemas

```python
import re
from pydantic import BaseModel, field_validator

HOSPITAL_ID_RE = re.compile(r"^[A-Za-z0-9]{2,16}$")

class TenantCreateRequest(BaseModel):
    hospital_id: str
    hospital_name: str

    @field_validator("hospital_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not HOSPITAL_ID_RE.match(v):
            raise ValueError("hospital_id must be 2-16 alphanumeric chars, no underscores")
        return v

    @field_validator("hospital_name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 100:
            raise ValueError("hospital_name required, 1..100 chars")
        return v

class TenantCreateResponse(BaseModel):
    created: bool
    hospital_id: str
    db_name: str
    hospital_name: str
    is_active: int
```

### 7.3 router

```python
from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_template_db
from app.modules.tenant import schemas, service
from app.utils.exceptions import UnauthorizedException, ValidationException

router = APIRouter()


def _require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if settings.ADMIN_TOKEN and x_admin_token != settings.ADMIN_TOKEN:
        raise UnauthorizedException(detail="Invalid admin token")


@router.post("", response_model=schemas.TenantCreateResponse)
def create_tenant(
    req: schemas.TenantCreateRequest,
    _admin: None = Depends(_require_admin),
    db: Session = Depends(get_template_db),
):
    return service.create_tenant(req, db)
```

### 7.4 service

```python
import logging
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.tenant.schemas import TenantCreateRequest, TenantCreateResponse

logger = logging.getLogger("tenant")


def create_tenant(req: TenantCreateRequest, template_db: Session) -> TenantCreateResponse:
    existing = template_db.execute(
        text("SELECT hospital_id, hospital_name, db_name, is_active "
             "FROM hospital_tenant WHERE hospital_id = :hid"),
        {"hid": req.hospital_id},
    ).fetchone()
    if existing:
        return TenantCreateResponse(
            created=False,
            hospital_id=existing.hospital_id,
            db_name=existing.db_name,
            hospital_name=existing.hospital_name,
            is_active=int(existing.is_active),
        )

    try:
        template_db.execute(
            text("CALL create_hospital_database(:hid)"),
            {"hid": req.hospital_id},
        )
    except Exception:
        logger.exception("CALL create_hospital_database failed for hospital_id=%s", req.hospital_id)
        raise

    db_name = f"hospital_{req.hospital_id}"
    try:
        template_db.execute(
            text("INSERT INTO hospital_tenant (hospital_id, hospital_name, db_name, is_active) "
                 "VALUES (:hid, :hname, :dbname, 1)"),
            {"hid": req.hospital_id, "hname": req.hospital_name, "dbname": db_name},
        )
        template_db.commit()
    except Exception:
        template_db.rollback()
        logger.warning(
            "hospital_tenant INSERT failed for %s; orphan database '%s' left behind",
            req.hospital_id, db_name,
        )
        raise

    return TenantCreateResponse(
        created=True,
        hospital_id=req.hospital_id,
        db_name=db_name,
        hospital_name=req.hospital_name,
        is_active=1,
    )
```

注意:`CALL` 抛出时,`raise` 让 FastAPI global exception handler 兜底返回 500;不显式 rollback(CALL 本身不可回滚,且未开 DML 事务)。

## 8. 配置

`backend/app/config.py` 的 `Settings` 类追加一行:

```python
ADMIN_TOKEN: str = ""   # 空=开放;填值=POST /tenants 共享密钥
```

## 9. 测试

手工验证脚本(写进设计但不作为 CI):

```bash
# 设置 token 并起 backend 后
curl -sX POST http://localhost:8000/api/v1/tenants \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $TOKEN" \
  -d '{"hospital_id":"H002","hospital_name":"示例医院"}'
# 期望: {"created":true,"hospital_id":"H002",...}

docker exec hospital-mysql mysql -uroot -proot -e \
  "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='hospital_H002';"
# 期望: 17

# 重复一次
curl -sX POST http://localhost:8000/api/v1/tenants .../d '{"hospital_id":"H002","hospital_name":"示例医院"}'
# 期望: {"created":false,"hospital_id":"H002",...}

# 上传到 H002 不再 1146 (测试由集成层做,本期不写)
```

单元测试(`backend/tests/modules/tenant/test_service.py`):
- `test_create_idempotent_returns_existing`:预置 hospital_tenant 行,调用 service,断言 created=false、返回原数据。
- `test_create_calls_then_inserts`:用 `unittest.mock.MagicMock` 包 Session,断言先 SELECT、再 CALL、再 INSERT、最后 commit。
- `test_create_insert_failure_warns_and_reraises`:让 INSERT 抛错,断言 rollback 被调用、异常被 reraise。
- `test_validator_rejects_underscore`:断言 schemas 校验拒绝 `H_002`。
- `test_validator_rejects_too_long`:断言 schemas 校验拒绝 17 字符 id。

router 层不单测,集成测试由手工 curl 覆盖。

## 10. 本期不做

- GET /tenants / PATCH /tenants/{id} / DELETE —— 后续加。
- 把 `hospital_H001` 的 DDL 与存储过程抽到共用 SQL 文件以彻底消除双写 —— 后续重构。
- 把 `02_hospital_created.sql` 自动 apply 到现有 MySQL 实例上(本期需要运维 `docker exec ... < 02_hospital_created.sql` 手动重载一次,见 §11)。
- 把孤儿库自动清理。
- Milvus namespace 在创建时初始化。

## 11. 部署步骤(上线时必做)

存储过程定义需要更新到 MySQL 里,而不是只改 SQL 文件:

```bash
docker exec -i hospital-mysql mysql -uroot -proot < infra/mysql/init/02_hospital_created.sql
```

如果新环境容器首次启动时已经会自动 mount `infra/mysql/init/`,则无需额外命令。但对于已运行的 `hospital-mysql` 容器,需手工 apply 一次让存储过程升级。注意 MySQL 的 `CREATE PROCEDURE IF NOT EXISTS` 只在过程**不存在**时才创建,**已存在**时是 no-op —— 也就是说它不会覆盖旧版定义。因此升级时必须先 DROP 一次再 source:

```bash
docker exec hospital-mysql mysql -uroot -proot hospital_template -e "DROP PROCEDURE IF EXISTS create_hospital_database;"
docker exec -i hospital-mysql mysql -uroot -proot < infra/mysql/init/02_hospital_created.sql
```

空库上(DROP 后立即 source)无副作用;有运行中租户时 DROP+recreate 仅替换过程定义,不触碰已建库的表结构。

完成后 backend 升级流程:重启 FastAPI(让 settings.ADMIN_TOKEN 生效即可)。

## 12. 兼容性影响

- 不改任何现有表 schema,`hospital_H001` 业务不需要迁移。
- `start.sh` 启动逻辑不变,首次空库初始化仍由它走原路径。
- 已存在租户重复调用接口 = 无副作用(幂等返回旧记录)。

## 13. 风险

| 风险 | 缓解 |
|---|---|
| 重复 id 时若已建库但未登记,接口返回 "不存在" 又走 CALL,CALL 幂等跳过建表,INSERT 报 PK 冲突 → 500 | 孤儿场景由 §4 WARN + 幂等 CALL 兜底,运维需介入登记;不自动删库 |
| `_require_admin` 空默认放行 = 开发环境靠 settings 控制 | 文档化:生产填 `ADMIN_TOKEN`,本地可留空 |
| 存储过程升级不到实例上 → 接口 500 报 `PROCEDURE ... does not exist` | §11 部署步骤明确手动升级动作 |
| hospital_id 含下划线会让 `batch_import_file` 文件名约定 `<姓名>_<医院编号>_<用户编号>` 误判 | schemas 的正则禁掉下划线 |