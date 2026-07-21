# 团体健康体检分析报告 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `statistics` 模块下新增 `group` 子命名空间,提供跨院团体分析概览 + 重点人群清单 + CSV 导出,前端在 admin-portal 新增 `/group-analysis` 页用 ECharts 展示。

**Architecture:** 后端 `group_router.py` → `group_service.py`(并发遍历各 tenant 库聚合,纯函数不持久化)→ `group_sql.py`(按维度生成 per-tenant `text()` SQL)。鉴权用 JWT + `require_role("admin")`(复用 `app/core/dependencies.py:39-43`),平台 admin `role='admin'` 且 `hospital_id=None`,显式调 `get_all_hospital_ids()` 跨库不读 ContextVar。前端用 admin-portal 现有 shared `apiClient`(`adminStore.token` 即 JWT)。

**Tech Stack:** FastAPI + SQLAlchemy(text() SQL)+ Pydantic v2 + pytest(SQLite in-memory + mock)+ React 18 + AntD 5 + echarts 5 + echarts-for-react。

**Spec:** `docs/superpowers/specs/2026-07-21-group-analysis-report-design.md`(已 commit `e89608b`)。

## Global Constraints

- Python 3.10 + Pydantic v2(`field_validator` 语法已用,见 `tenant/schemas.py`)
- 测试体系:pytest,SQLite in-memory + MagicMock Session(参考 `backend/tests/modules/tenant/test_service.py` 与 `backend/tests/test_batch_service.py`)
- 单元测试文件路径:`backend/tests/modules/statistics/test_group_*.py`(目录需创建,补 `__init__.py`)
- 测试运行:`cd backend && .venv/bin/pytest tests/modules/statistics/test_group_*.py -v`
- 接口路径前缀:`/api/v1/statistics/group/*`(挂在现有 statistics prefix 下)
- 鉴权依赖:`from app.core.dependencies import require_role` 然后 `Depends(require_role("admin"))` —— **不**新建 admin_token 依赖
- 跨库获取活跃 tenant:`from app.core.database import get_all_hospital_ids, get_session`
- per-tenant session 开法:`db = get_session(f"hospital_{hid}")` 然后 `try: ... finally: db.close()`(见 `database.py:60-67`)
- 不动 DDL,不新增 ORM 模型,SQL 全用 `text()`(沿用 `statistics/service.py` 风格)
- 日期维度分组用 dialect-aware:`db.bind.dialect.name == "mysql"` 用 `DATE_FORMAT(report_date,'%Y-%m')`,SQLite 用 `strftime('%Y-%m', report_date)`(单元测试要在 SQLite 下能跑)
- 提交粒度:每个 Task 完成后单独 commit
- 时间日期 Python 端用 `datetime.date` 与 ISO `YYYY-MM-DD` 字符串

---

## 文件结构

```
backend/app/modules/statistics/
├── router.py            (existing;不动)
├── service.py           (existing;不动)
├── group_router.py      (新增;2 endpoint + 鉴权)
├── group_service.py     (新增;ThreadPool 跨库聚合 + merge + CSV 流)
├── group_sql.py         (新增;按维度生成 text() SQL 与 params)
└── group_schemas.py     (新增;GroupBy enum / Filters / Row / Item)

backend/tests/modules/statistics/
├── __init__.py          (新增;空)
├── test_group_schemas.py    (Task 1)
├── test_group_sql.py        (Task 2, 3)
├── test_group_service.py    (Task 4, 5, 6, 7)
└── test_group_router.py     (Task 8)

backend/app/main.py      (modify Task 8;include 新 router)

frontend/packages/admin-portal/
├── package.json         (modify Task 9;加 echarts/echarts-for-react)
├── vite.config.ts       (modify Task 9;加 proxy)
├── src/router.tsx       (modify Task 9;加 /group-analysis 路由)
└── src/
    ├── api/groupAnalysis.ts      (新增;Task 9)
    └── pages/group-analysis/
        ├── GroupAnalysisPage.tsx (新增;Task 10)
        └── components/
            ├── FilterBar.tsx     (新增;Task 10)
            ├── OverviewCharts.tsx(新增;Task 10)
            └── HighRiskTable.tsx (新增;Task 10)
```

文件职责边界:
- `group_schemas.py` —— 仅 Pydantic 类型与 query/响应模型,无业务
- `group_sql.py` —— 纯函数,返回 `(sql_str, params)` 元组;无 DB 调用、无 IO
- `group_service.py` —— 编排跨库查询、并发、merge、CSV 流;依赖 `group_sql` 与 `database.py`
- `group_router.py` —— FastAPI endpoint,鉴权与参数解析,调 `group_service`
- 前端 `api/groupAnalysis.ts` —— 仅 HTTP 调用与 TS 类型
- `GroupAnalysisPage.tsx` —— 容器,状态 + tab 切换
- `FilterBar.tsx` / `OverviewCharts.tsx` / `HighRiskTable.tsx` —— 纯展示组件,props-in

---

## Task 1: Pydantic schemas(`group_schemas.py`)

**Files:**
- Create: `backend/app/modules/statistics/group_schemas.py`
- Create: `backend/tests/modules/statistics/__init__.py`(空文件)
- Create: `backend/tests/modules/statistics/test_group_schemas.py`

**Interfaces:**
- Produces:
  - `GroupBy` — `Literal["hospital","batch","age_group","gender","time_month"]`(也可做 Enum 但 Literal 更轻)
  - `GroupFilters` — dataclass-like pydantic `BaseModel`,字段:`hospital_ids: list[str] | None`、`batch_ids: list[str] | None`、`date_from: date | None`、`date_to: date | None`、`gender: Literal["M","F"] | None`、`age_groups: list[str] | None`、`topn: int = 10`
  - `OverviewRow` — pydantic BaseModel
  - `OverviewResponse` — pydantic BaseModel
  - `HighRiskItem` / `HighRiskResponse` — pydantic BaseModel
  - 函数 `parse_csv_query(value: str | None) -> list[str] | None` —— 把请求 query 的 csv 字符串解析成 list,空返回 None

- [ ] **Step 1: 写失败的测试**

`backend/tests/modules/statistics/test_group_schemas.py`:
```python
import pytest
from datetime import date
from pydantic import ValidationError

from app.modules.statistics.group_schemas import (
    GroupFilters, GroupBy, parse_csv_query,
)


def test_parse_csv_query_none_returns_none():
    assert parse_csv_query(None) is None
    assert parse_csv_query("") is None


def test_parse_csv_query_strips_and_dedups():
    assert parse_csv_query("H001, H002 ,H001") == ["H001", "H002"]


def test_group_filters_defaults():
    f = GroupFilters()
    assert f.hospital_ids is None
    assert f.batch_ids is None
    assert f.date_from is None
    assert f.date_to is None
    assert f.gender is None
    assert f.age_groups is None
    assert f.topn == 10


def test_group_filters_gender_invalid_rejected():
    with pytest.raises(ValidationError):
        GroupFilters(gender="X")


def test_group_filters_age_groups_keeps_order():
    f = GroupFilters(age_groups=["30-39", "40-49"])
    assert f.age_groups == ["30-39", "40-49"]


def test_groupby_literal_accepts_known():
    for v in ("hospital", "batch", "age_group", "gender", "time_month"):
        assert v in GroupBy.__args__  # Literal exposes via __args__
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_schemas.py -v`
Expected: ImportError on `app.modules.statistics.group_schemas`

- [ ] **Step 3: 写最小实现**

`backend/app/modules/statistics/group_schemas.py`:
```python
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

GroupBy = Literal["hospital", "batch", "age_group", "gender", "time_month"]
Gender = Literal["M", "F"]
AgeGroup = Literal["<20", "20-29", "30-39", "40-49", "50-59", "60+"]
SortKey = Literal["red_count", "age", "report_date"]
ExportFormat = Literal["json", "csv"]


def parse_csv_query(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    seen: list[str] = []
    for tok in value.split(","):
        t = tok.strip()
        if t and t not in seen:
            seen.append(t)
    return seen or None


class GroupFilters(BaseModel):
    hospital_ids: Optional[list[str]] = None
    batch_ids: Optional[list[str]] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    gender: Optional[Gender] = None
    age_groups: Optional[list[str]] = None
    topn: int = 10


class OverviewRow(BaseModel):
    key: str
    label: str
    total_people: int = 0
    red_count: int = 0
    yellow_count: int = 0
    green_count: int = 0
    abnormal_rate: float = 0.0
    by_gender: Optional[list[dict]] = None
    by_age_group: Optional[list[dict]] = None
    top_abnormal_items: Optional[list[dict]] = None
    error: Optional[str] = None


class OverviewResponse(BaseModel):
    group_by: str
    filters: dict
    rows: list[OverviewRow]
    totals: dict


class HighRiskItem(BaseModel):
    hospital_id: str
    hospital_name: str
    report_id: int
    user_id: int
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    report_date: Optional[date] = None
    batch_id: Optional[str] = None
    batch_name: Optional[str] = None
    overall_level: Optional[str] = None
    red_count: int = 0
    yellow_count: int = 0
    summary_text: Optional[str] = None


class HighRiskResponse(BaseModel):
    items: list[HighRiskItem]
    total: int
    page: int
    page_size: int
    filters: dict
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_schemas.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/statistics/group_schemas.py \
        backend/tests/modules/statistics/__init__.py \
        backend/tests/modules/statistics/test_group_schemas.py
git commit -m "feat(statistics): group analysis schemas (GroupBy/Filters/Row/Item)"
```

---

## Task 2: SQL builder —— 跨维度核心聚合 `build_overview_sql`

**Files:**
- Create: `backend/app/modules/statistics/group_sql.py`
- Create: `backend/tests/modules/statistics/test_group_sql.py`

**Interfaces:**
- Consumes: `group_schemas.GroupBy`, `group_schemas.GroupFilters`
- Produces:
  - `build_overview_sql(group_by: GroupBy, filters: GroupFilters, dialect: str) -> tuple[str, dict]` —— 返回 `(sql, params)`;SQL 以 `:param` 命名(MySQL/SQLite 通用);dialect ∈ `{"mysql","sqlite"}` 决定 `time_month` 的日期格式化函数。

- [ ] **Step 1: 写失败的测试**

`backend/tests/modules/statistics/test_group_sql.py`:
```python
from datetime import date
from app.modules.statistics.group_schemas import GroupFilters
from app.modules.statistics.group_sql import build_overview_sql


def test_overview_hospital_minimal_mysql():
    f = GroupFilters()
    sql, p = build_overview_sql("hospital", f, dialect="mysql")
    # 单库聚合:核心 5 指标,无 GROUP BY(每库一行,Python 跨库组装)
    assert "COUNT(DISTINCT ri.id)" in sql
    assert "report_interpretation" in sql and "interp" in sql
    assert "overall_level='red'" in sql
    assert "GROUP BY" not in sql  # hospital 维度不附加 GROUP BY
    assert p == {}
    assert ":date_from" not in sql and ":date_to" not in sql


def test_overview_hospital_minimal_sqlite():
    f = GroupFilters()
    sql, _ = build_overview_sql("hospital", f, dialect="sqlite")
    assert "strftime" not in sql  # hospital 不需要日期函数


def test_overview_with_date_filter():
    f = GroupFilters(date_from=date(2026, 1, 1), date_to=date(2026, 6, 30))
    sql, p = build_overview_sql("hospital", f, dialect="mysql")
    assert "ri.report_date >= :date_from" in sql
    assert "ri.report_date <= :date_to" in sql
    assert p["date_from"] == "2026-01-01"
    assert p["date_to"] == "2026-06-30"


def test_overview_with_gender_filter():
    f = GroupFilters(gender="M")
    sql, p = build_overview_sql("hospital", f, dialect="mysql")
    assert "ri.gender = :gender" in sql
    assert p["gender"] == "M"


def test_overview_with_age_groups_filter():
    f = GroupFilters(age_groups=["30-39", "40-49"])
    sql, p = build_overview_sql("hospital", f, dialect="mysql")
    # age_groups 转成 IN list bound params
    assert ":age_0" in sql and ":age_1" in sql
    assert p["age_0"] == "30-39"
    assert p["age_1"] == "40-49"


def test_overview_age_group_dimension_adds_groupby():
    f = GroupFilters()
    sql, _ = build_overview_sql("age_group", f, dialect="mysql")
    assert "GROUP BY age_group" in sql
    assert "CASE" in sql and "WHEN ri.age < 20 THEN '<20'" in sql


def test_overview_gender_dimension_adds_groupby():
    f = GroupFilters()
    sql, _ = build_overview_sql("gender", f, dialect="mysql")
    assert "GROUP BY ri.gender" in sql


def test_overview_time_month_mysql_uses_date_format():
    sql, _ = build_overview_sql("time_month", GroupFilters(), dialect="mysql")
    assert "DATE_FORMAT(ri.report_date,'%Y-%m')" in sql
    assert "GROUP BY ym" in sql


def test_overview_time_month_sqlite_uses_strftime():
    sql, _ = build_overview_sql("time_month", GroupFilters(), dialect="sqlite")
    assert "strftime('%Y-%m', ri.report_date)" in sql
    assert "GROUP BY ym" in sql


def test_overview_batch_dimension_joins_batch_tables():
    sql, _ = build_overview_sql("batch", GroupFilters(), dialect="mysql")
    assert "LEFT JOIN batch_import_file bf ON bf.report_task_id = ri.task_id" in sql
    assert "LEFT JOIN batch_import b ON b.id = bf.batch_id" in sql
    assert "GROUP BY b.id" in sql


def test_overview_batch_ids_filter_when_batch_ids_provided():
    f = GroupFilters(batch_ids=["uuid1", "uuid2"])
    sql, p = build_overview_sql("hospital", f, dialect="mysql")
    assert "b.id IN (:batch_0, :batch_1)" in sql
    assert p["batch_0"] == "uuid1"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_sql.py -v`
Expected: ImportError on `app.modules.statistics.group_sql`

- [ ] **Step 3: 写最小实现**

`backend/app/modules/statistics/group_sql.py`:
```python
from datetime import date
from typing import Any

from app.modules.statistics.group_schemas import GroupBy, GroupFilters

AGE_GROUP_CASE = (
    "CASE "
    "WHEN ri.age < 20 THEN '<20' "
    "WHEN ri.age BETWEEN 20 AND 29 THEN '20-29' "
    "WHEN ri.age BETWEEN 30 AND 39 THEN '30-39' "
    "WHEN ri.age BETWEEN 40 AND 49 THEN '40-49' "
    "WHEN ri.age BETWEEN 50 AND 59 THEN '50-59' "
    "WHEN ri.age >= 60 THEN '60+' "
    "END"
)

BATCH_JOIN = (
    "LEFT JOIN batch_import_file bf ON bf.report_task_id = ri.task_id "
    "LEFT JOIN batch_import b ON b.id = bf.batch_id "
)


def _where(filters: GroupFilters) -> tuple[str, dict]:
    clauses = ["interp.status = 'completed'"]
    params: dict[str, Any] = {}
    if filters.date_from:
        clauses.append("ri.report_date >= :date_from")
        params["date_from"] = filters.date_from.isoformat()
    if filters.date_to:
        clauses.append("ri.report_date <= :date_to")
        params["date_to"] = filters.date_to.isoformat()
    if filters.gender:
        clauses.append("ri.gender = :gender")
        params["gender"] = filters.gender
    if filters.age_groups:
        ph = [f":age_{i}" for i in range(len(filters.age_groups))]
        clauses.append(f"({AGE_GROUP_CASE}) IN ({', '.join(ph)})")
        for i, v in enumerate(filters.age_groups):
            params[f"age_{i}"] = v
    if filters.batch_ids:
        ph = [f":batch_{i}" for i in range(len(filters.batch_ids))]
        clauses.append(f"b.id IN ({', '.join(ph)})")
        for i, v in enumerate(filters.batch_ids):
            params[f"batch_{i}"] = v
    return " AND ".join(clauses), params


def build_overview_sql(group_by: GroupBy, filters: GroupFilters, dialect: str) -> tuple[str, dict]:
    needs_batch = group_by == "batch" or bool(filters.batch_ids)
    base = (
        "SELECT "
        "  COUNT(DISTINCT ri.id) AS total_people, "
        "  SUM(CASE WHEN interp.overall_level='red' THEN 1 ELSE 0 END) AS red_count, "
        "  SUM(CASE WHEN interp.overall_level='yellow' THEN 1 ELSE 0 END) AS yellow_count, "
        "  SUM(CASE WHEN interp.overall_level='green' THEN 1 ELSE 0 END) AS green_count "
        "FROM report_info ri "
        "JOIN report_interpretation interp ON interp.report_id = ri.id "
    )
    if needs_batch:
        base += BATCH_JOIN

    select_dim = ""
    group_dim = ""
    if group_by == "age_group":
        select_dim = f", {AGE_GROUP_CASE} AS age_group"
        group_dim = " GROUP BY age_group"
    elif group_by == "gender":
        select_dim = ", ri.gender AS gender"
        group_dim = " GROUP BY ri.gender"
    elif group_by == "batch":
        select_dim = ", b.id AS batch_id, b.filename AS batch_name"
        group_dim = " GROUP BY b.id, b.filename"
    elif group_by == "time_month":
        fn = "DATE_FORMAT(ri.report_date,'%Y-%m')" if dialect == "mysql" \
            else "strftime('%Y-%m', ri.report_date)"
        select_dim = f", {fn} AS ym"
        group_dim = " GROUP BY ym"

    where, params = _where(filters)
    sql = base + select_dim + " WHERE " + where + group_dim
    return sql, params
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_sql.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/statistics/group_sql.py \
        backend/tests/modules/statistics/test_group_sql.py
git commit -m "feat(statistics): group overview SQL builder (5 dim + filters)"
```

---

## Task 3: SQL builder —— Top 异常指标 + 高风险清单 + 性别/年龄 sub-distribution

**Files:**
- Modify: `backend/app/modules/statistics/group_sql.py`
- Modify: `backend/tests/modules/statistics/test_group_sql.py`

**Interfaces:**
- Produces:
  - `build_top_abnormal_sql(filters: GroupFilters, topn: int) -> tuple[str, dict]` —— Top 异常指标(按 red 计数 desc)
  - `build_sub_gender_sql(filters: GroupFilters) -> tuple[str, dict]` —— 该库性别分布
  - `build_sub_age_group_sql(filters: GroupFilters) -> tuple[str, dict]` —— 该库年龄段分布
  - `build_high_risk_list_sql(filters: GroupFilters, sort: str, limit: int, offset: int, count_only: bool = False) -> tuple[str, dict]`

- [ ] **Step 1: 写失败的测试(追加到 test_group_sql.py)**

```python
from app.modules.statistics.group_sql import (
    build_top_abnormal_sql, build_sub_gender_sql,
    build_sub_age_group_sql, build_high_risk_list_sql,
)


def test_top_abnormal_sql_basic():
    sql, p = build_top_abnormal_sql(GroupFilters(), topn=5)
    assert "ij.item_name" in sql and "ij.color_level = 'red'" in sql
    assert "ORDER BY red_cnt DESC" in sql
    assert "LIMIT :topn" in sql
    assert p["topn"] == 5


def test_sub_gender_sql():
    sql, _ = build_sub_gender_sql(GroupFilters())
    assert "ri.gender AS gender" in sql and "GROUP BY ri.gender" in sql
    assert "COUNT(*) AS cnt" in sql


def test_sub_age_group_sql():
    sql, _ = build_sub_age_group_sql(GroupFilters())
    assert "CASE WHEN ri.age < 20" in sql
    assert "GROUP BY age_group" in sql


def test_high_risk_list_basic():
    sql, p = build_high_risk_list_sql(GroupFilters(), sort="red_count",
                                      limit=20, offset=0)
    assert "interp.overall_level = 'red' OR interp.red_count >= 3" in sql
    assert "ri.id AS report_id" in sql
    assert "ri.name" in sql  # name from report_info not hospital_user
    assert "ORDER BY interp.red_count DESC" in sql
    assert "LIMIT :limit OFFSET :offset" in sql
    assert "JOIN hospital_user" not in sql  # 不 join hospital_user
    assert p["limit"] == 20 and p["offset"] == 0


def test_high_risk_list_sort_report_date():
    sql, _ = build_high_risk_list_sql(GroupFilters(), sort="report_date",
                                      limit=10, offset=0)
    assert "ORDER BY ri.report_date DESC" in sql


def test_high_risk_count_only():
    sql, p = build_high_risk_list_sql(GroupFilters(), sort="red_count",
                                      limit=20, offset=0, count_only=True)
    assert sql.strip().startswith("SELECT COUNT(*)")
    assert "LIMIT :limit" not in sql and "OFFSET" not in sql
    # count_only 不需要 limit/offset params
    assert "limit" not in p and "offset" not in p


def test_high_risk_list_batch_filter_join():
    f = GroupFilters(batch_ids=["x1"])
    sql, _ = build_high_risk_list_sql(f, sort="red_count", limit=10, offset=0)
    assert "LEFT JOIN batch_import_file bf" in sql
    assert "LEFT JOIN batch_import b" in sql
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_sql.py -v`
Expected: ImportError on `build_top_abnormal_sql`

- [ ] **Step 3: 实现追加到 group_sql.py**

```python
def build_top_abnormal_sql(filters: GroupFilters, topn: int) -> tuple[str, dict]:
    needs_batch = bool(filters.batch_ids)
    sql = (
        "SELECT ij.item_name AS item, "
        "  SUM(CASE WHEN ij.color_level='red' THEN 1 ELSE 0 END) AS red_cnt "
        "FROM indicator_judgment ij "
        "JOIN report_interpretation interp ON interp.id = ij.interpretation_id "
        "JOIN report_info ri ON ri.id = interp.report_id "
    )
    if needs_batch:
        sql += BATCH_JOIN
    where, params = _where(filters)
    sql += " WHERE ij.color_level = 'red' AND " + where + \
           " GROUP BY ij.item_name ORDER BY red_cnt DESC LIMIT :topn"
    params["topn"] = topn
    return sql, params


def build_sub_gender_sql(filters: GroupFilters) -> tuple[str, dict]:
    needs_batch = bool(filters.batch_ids)
    sql = (
        "SELECT ri.gender AS gender, COUNT(*) AS cnt "
        "FROM report_info ri "
        "JOIN report_interpretation interp ON interp.report_id = ri.id "
    )
    if needs_batch:
        sql += BATCH_JOIN
    where, params = _where(filters)
    sql += " WHERE " + where + " GROUP BY ri.gender"
    return sql, params


def build_sub_age_group_sql(filters: GroupFilters) -> tuple[str, dict]:
    needs_batch = bool(filters.batch_ids)
    sql = (
        f"SELECT {AGE_GROUP_CASE} AS age_group, COUNT(*) AS cnt "
        "FROM report_info ri "
        "JOIN report_interpretation interp ON interp.report_id = ri.id "
    )
    if needs_batch:
        sql += BATCH_JOIN
    where, params = _where(filters)
    sql += " WHERE " + where + " GROUP BY age_group"
    return sql, params


_SORT_COL = {
    "red_count": "interp.red_count",
    "age": "ri.age",
    "report_date": "ri.report_date",
}


def build_high_risk_list_sql(filters: GroupFilters, sort: str,
                              limit: int, offset: int,
                              count_only: bool = False) -> tuple[str, dict]:
    needs_batch = bool(filters.batch_ids)
    where, params = _where(filters)
    where = where + " AND (interp.overall_level = 'red' OR interp.red_count >= 3)"
    if count_only:
        sql = "SELECT COUNT(*) FROM report_info ri " \
              "JOIN report_interpretation interp ON interp.report_id = ri.id "
        if needs_batch:
            sql += BATCH_JOIN
        sql += " WHERE " + where
        return sql, params
    sort_col = _SORT_COL.get(sort, "interp.red_count")
    sql = (
        "SELECT ri.id AS report_id, ri.user_id AS user_id, ri.name AS name, "
        "ri.gender AS gender, ri.age AS age, ri.report_date AS report_date, "
        "b.id AS batch_id, b.filename AS batch_name, "
        "interp.overall_level AS overall_level, "
        "interp.red_count AS red_count, interp.yellow_count AS yellow_count, "
        "interp.summary_text AS summary_text "
        "FROM report_info ri "
        "JOIN report_interpretation interp ON interp.report_id = ri.id "
    )
    if needs_batch:
        sql += BATCH_JOIN
    sql += " WHERE " + where + f" ORDER BY {sort_col} DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    return sql, params
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_sql.py -v`
Expected: all passed(原 11 + 新 7 = 18)

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/statistics/group_sql.py \
        backend/tests/modules/statistics/test_group_sql.py
git commit -m "feat(statistics): top items + sub-distribution + high-risk SQL"
```

---

## Task 4: per-tenant overview fetcher + 单库失败 catch

**Files:**
- Create: `backend/app/modules/statistics/group_service.py`
- Create: `backend/tests/modules/statistics/test_group_service.py`

**Interfaces:**
- Consumes:
  - `group_sql.build_overview_sql(group_by, filters, dialect)` → `(sql, params)`
  - `group_sql.build_top_abnormal_sql(filters, topn)` / `build_sub_gender_sql` / `build_sub_age_group_sql`
  - SQLAlchemy `Session.execute(text(sql), params).fetchone() / .fetchall()`
  - `Session.bind.dialect.name` 取 dialect
- Produces:
  - `def _per_tenant_overview(hid: str, hname: str, group_by: GroupBy,
                              filters: GroupFilters) -> dict`
    - 单库执行 + 异常 catch;失败返回 `{"key": hid, "label": hname, "error": "db_unavailable"}`
    - 成功:
      - `group_by="hospital"` 单行 + 附 `by_gender`/`by_age_group`/`top_abnormal_items`
      - 其它 `group_by` 多行,每行含 `key`/`label`/5 指标
  - `def _row_key_label(group_by, row, hid, hname) -> tuple[str, str]`(helper)

- [ ] **Step 1: 写失败测试**

`backend/tests/modules/statistics/test_group_service.py`:
```python
from datetime import date
from unittest.mock import MagicMock, patch
from sqlalchemy import text

from app.modules.statistics.group_schemas import GroupFilters
from app.modules.statistics.group_service import _per_tenant_overview, _row_key_label


def _row(**kw):
    r = MagicMock()
    for k, v in kw.items():
        setattr(r, k, v)
    # fetchone() / fetchall() 返回值由调用方单独 stub
    return r


def test_per_tenant_overview_hospital_success():
    f = GroupFilters()
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    db.execute.side_effect = [
        MagicMock(fetchone=lambda: _row(total_people=100, red_count=10,
                                         yellow_count=20, green_count=70)),
        MagicMock(fetchall=lambda: [_row(gender="M", cnt=55), _row(gender="F", cnt=45)]),
        MagicMock(fetchall=lambda: [_row(age_group="30-39", cnt=40)]),
        MagicMock(fetchall=lambda: [_row(item="BMI", red_cnt=8)]),
    ]
    with patch("app.modules.statistics.group_service.get_session",
               return_value=db):
        res = _per_tenant_overview("H001", "杭州第一医院", "hospital", f)
    assert res["key"] == "H001" and res["label"] == "杭州第一医院"
    assert res["total_people"] == 100
    assert res["red_count"] == 10 and res["yellow_count"] == 20 and res["green_count"] == 70
    assert round(res["abnormal_rate"], 3) == 0.3
    assert res["by_gender"] == [{"key": "M", "count": 55}, {"key": "F", "count": 45}]
    assert res["by_age_group"] == [{"key": "30-39", "count": 40}]
    assert res["top_abnormal_items"] == [{"item": "BMI", "red_count": 8}]


def test_per_tenant_overview_db_failure_returns_error_row():
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    db.execute.side_effect = RuntimeError("connect refused")
    with patch("app.modules.statistics.group_service.get_session",
               return_value=db):
        res = _per_tenant_overview("H002", "示例医院", "hospital", GroupFilters())
    assert res["key"] == "H002"
    assert res["error"] == "db_unavailable"
    assert "total_people" not in res


def test_per_tenant_overview_age_group_multi_rows():
    f = GroupFilters()
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    db.execute.side_effect = [
        MagicMock(fetchall=lambda: [
            _row(age_group="30-39", total_people=40, red_count=4,
                 yellow_count=10, green_count=26),
            _row(age_group="40-49", total_people=60, red_count=9,
                 yellow_count=18, green_count=33),
        ]),
    ]
    with patch("app.modules.statistics.group_service.get_session", return_value=db):
        res = _per_tenant_overview("H001", "H", "age_group", f)
    # age_group 维度返回多行 list(由 service 把 list 入 rows);这里 _per_tenant 返回 list[dict]
    assert isinstance(res, list) and len(res) == 2
    assert res[0]["key"] == "30-39" and res[0]["total_people"] == 40


def test_row_key_label_hospital():
    assert _row_key_label("hospital", None, "H001", "杭州第一医院") == ("H001", "杭州第一医院")


def test_row_key_label_age_group():
    assert _row_key_label("age_group", _row(age_group="40-49"), "H", "H") == ("40-49", "40-49")


def test_row_key_label_batch():
    assert _row_key_label("batch", _row(batch_id="u1", batch_name="f.zip"), "H", "H") == ("u1", "f.zip")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_service.py -v`
Expected: ImportError on `group_service`

- [ ] **Step 3: 实现**

`backend/app/modules/statistics/group_service.py`:
```python
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.statistics.group_schemas import GroupBy, GroupFilters
from app.modules.statistics.group_sql import (
    build_overview_sql, build_top_abnormal_sql,
    build_sub_gender_sql, build_sub_age_group_sql,
    build_high_risk_list_sql,
)
from app.core.database import get_session, get_all_hospital_ids

logger = logging.getLogger("app.statistics")

_HOSPITAL_DIMS = {"hospital"}
_MULTIROW_DIMS = {"batch", "age_group", "gender", "time_month"}


def _row_key_label(group_by: GroupBy, row, hid: str, hname: str) -> tuple[str, str]:
    if group_by == "hospital":
        return hid, hname
    if group_by == "age_group":
        return row.age_group, row.age_group
    if group_by == "gender":
        return row.gender or "未知", row.gender or "未知"
    if group_by == "batch":
        return row.batch_id, row.batch_name or row.batch_id
    if group_by == "time_month":
        return row.ym, row.ym
    return str(hid), str(hname)


def _abnormal_rate(red: int, yellow: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((red + yellow) / total, 4)


def _per_tenant_overview(hid: str, hname: str, group_by: GroupBy,
                          filters: GroupFilters) -> dict | list[dict] | None:
    db = get_session(f"hospital_{hid}")
    try:
        dialect = db.bind.dialect.name
        sql, params = build_overview_sql(group_by, filters, dialect)
        if group_by in _MULTIROW_DIMS:
            rows = db.execute(text(sql), params).fetchall()
            out: list[dict] = []
            for r in rows:
                key, label = _row_key_label(group_by, r, hid, hname)
                out.append({
                    "key": key, "label": label,
                    "total_people": r.total_people or 0,
                    "red_count": r.red_count or 0,
                    "yellow_count": r.yellow_count or 0,
                    "green_count": r.green_count or 0,
                    "abnormal_rate": _abnormal_rate(r.red_count or 0,
                                                    r.yellow_count or 0,
                                                    r.total_people or 0),
                })
            return out
        # 单行(hospital)
        one = db.execute(text(sql), params).fetchone()
        if one is None:
            return {"key": hid, "label": hname,
                    "total_people": 0, "red_count": 0, "yellow_count": 0,
                    "green_count": 0, "abnormal_rate": 0.0,
                    "by_gender": [], "by_age_group": [], "top_abnormal_items": []}
        total = one.total_people or 0
        red = one.red_count or 0
        yellow = one.yellow_count or 0
        green = one.green_count or 0
        # sub-distribution / top items(仅 hospital 维度)
        by_gender = []
        gsql, gp = build_sub_gender_sql(filters)
        for r in db.execute(text(gsql), gp).fetchall():
            by_gender.append({"key": r.gender or "未知", "count": r.cnt})
        by_age_group = []
        asql, ap = build_sub_age_group_sql(filters)
        for r in db.execute(text(asql), ap).fetchall():
            by_age_group.append({"key": r.age_group, "count": r.cnt})
        top_abnormal = []
        tsql, tp = build_top_abnormal_sql(filters, filters.topn)
        for r in db.execute(text(tsql), tp).fetchall():
            top_abnormal.append({"item": r.item, "red_count": r.red_cnt})
        return {
            "key": hid, "label": hname,
            "total_people": total, "red_count": red, "yellow_count": yellow,
            "green_count": green,
            "abnormal_rate": _abnormal_rate(red, yellow, total),
            "by_gender": by_gender, "by_age_group": by_age_group,
            "top_abnormal_items": top_abnormal,
        }
    except Exception:
        logger.exception("group_overview hid=%s failed", hid)
        return {"key": hid, "label": hname, "error": "db_unavailable"}
    finally:
        db.close()
```

注意:`get_session` 在测试 patch 时只 patch `app.modules.statistics.group_service.get_session`,因实现里 `from app.core.database import get_session`。

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_service.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/statistics/group_service.py \
        backend/tests/modules/statistics/test_group_service.py
git commit -m "feat(statistics): per-tenant overview fetcher + db_unavailable catch"
```

---

## Task 5: 跨库编排 `get_overview` + ThreadPool + merge totals

**Files:**
- Modify: `backend/app/modules/statistics/group_service.py`
- Modify: `backend/tests/modules/statistics/test_group_service.py`

**Interfaces:**
- Consumes:
  - `get_all_hospital_ids() -> list[str]`(from `app.core.database`)
  - `hospital_tenant` 表:`hospital_name` 需要;为此加 `_get_tenant_names(hids) -> dict[str,str]`(查 `hospital_template.hospital_tenant`)
  - `_per_tenant_overview` (Task 4)
- Produces:
  - `def get_overview(group_by: GroupBy, filters: GroupFilters) -> dict`
    - 返回 `OverviewResponse` schema dict
    - 当 `filters.hospital_ids` 给定时只跑这些;否则全活跃
    - `ThreadPoolExecutor(max_workers=8)` 并发
    - merge:把 per-tenant list/单-row 摊平到 `rows`(按 dict)
    - `totals`:跨所有 row 加和(忽略 error row)

- [ ] **Step 1: 写失败测试(追加到 test_group_service.py)**

```python
from app.modules.statistics.group_service import get_overview


def test_get_overview_merges_multi_tenant(monkeypatch):
    # 准备 tenant 数据
    monkeypatch.setattr("app.modules.statistics.group_service.get_all_hospital_ids",
                         lambda: ["H001", "H002"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "杭州第一医院", "H002": "示例医院"})
    calls = []

    def fake_per(hid, hname, group_by, filters):
        calls.append(hid)
        return {"key": hid, "label": hname,
                "total_people": 100 if hid == "H001" else 50,
                "red_count": 10 if hid == "H001" else 5,
                "yellow_count": 20 if hid == "H001" else 10,
                "green_count": 70 if hid == "H001" else 35,
                "abnormal_rate": 0.3,
                "by_gender": [], "by_age_group": [], "top_abnormal_items": []}

    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_overview",
                         fake_per)
    r = get_overview("hospital", GroupFilters())
    assert r["group_by"] == "hospital"
    assert len(r["rows"]) == 2
    assert r["rows"][0]["key"] in {"H001", "H002"}
    assert r["totals"]["total_people"] == 150
    assert r["totals"]["red_count"] == 15
    assert r["totals"]["yellow_count"] == 30
    assert r["totals"]["green_count"] == 105
    assert "abnormal_rate" in r["totals"]


def test_get_overview_filters_hospital_ids(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service.get_all_hospital_ids",
                         lambda: ["H001", "H002", "H003"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {h: h for h in hids})
    calls = []

    def fake_per(hid, hname, group_by, filters):
        calls.append(hid)
        return {"key": hid, "label": hname,
                "total_people": 1, "red_count": 1, "yellow_count": 0,
                "green_count": 0, "abnormal_rate": 1.0}

    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_overview",
                         fake_per)
    get_overview("hospital", GroupFilters(hospital_ids=["H002"]))
    assert calls == ["H002"]


def test_get_overview_age_group_flatten_multirow_rows(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service.get_all_hospital_ids",
                         lambda: ["H001"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "H"})
    def fake_per(hid, hname, gb, f):
        return [
            {"key": "30-39", "label": "30-39", "total_people": 10,
             "red_count": 1, "yellow_count": 2, "green_count": 7,
             "abnormal_rate": 0.3},
            {"key": "40-49", "label": "40-49", "total_people": 20,
             "red_count": 2, "yellow_count": 4, "green_count": 14,
             "abnormal_rate": 0.3},
        ]
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_overview",
                         fake_per)
    r = get_overview("age_group", GroupFilters())
    assert len(r["rows"]) == 2
    assert r["rows"][0]["key"] in {"30-39", "40-49"}
    assert r["totals"]["total_people"] == 30


def test_get_overview_db_unavailable_skipped_in_totals(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service.get_all_hospital_ids",
                         lambda: ["H001", "H002"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "A", "H002": "B"})
    def fake_per(hid, hname, gb, f):
        if hid == "H002":
            return {"key": hid, "label": hname, "error": "db_unavailable"}
        return {"key": hid, "label": hname,
                "total_people": 100, "red_count": 10, "yellow_count": 20,
                "green_count": 70, "abnormal_rate": 0.3}
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_overview",
                         fake_per)
    r = get_overview("hospital", GroupFilters())
    assert any(row.get("error") == "db_unavailable" for row in r["rows"])
    assert r["totals"]["total_people"] == 100  # H002 不计入


def test_get_overview_empty_tenants(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service.get_all_hospital_ids",
                         lambda: [])
    r = get_overview("hospital", GroupFilters())
    assert r["rows"] == []
    assert r["totals"]["total_people"] == 0
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_service.py::test_get_overview_merges_multi_tenant -v`
Expected: `get_overview` ImportError or name not defined

- [ ] **Step 3: 实现(追加到 group_service.py)**

```python
from sqlalchemy import bindparam  # 文件顶部已有 text import;追加 bindparam

MAX_WORKERS = 8


def _get_tenant_names(hids: list[str]) -> dict[str, str]:
    """从 hospital_template.hospital_tenant 表批量取 hospital_name"""
    if not hids:
        return {}
    from app.config import settings
    db = get_session(settings.MYSQL_TEMPLATE_DB)
    try:
        rows = db.execute(
            text("SELECT hospital_id, hospital_name FROM hospital_tenant "
                 "WHERE hospital_id IN :ids")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": hids},
        ).fetchall()
        return {r.hospital_id: r.hospital_name for r in rows}
    except Exception:
        logger.exception("_get_tenant_names failed; fallback to id as name")
        return {h: h for h in hids}
    finally:
        db.close()


def _resolve_tenants(filters: GroupFilters) -> list[str]:
    all_ids = get_all_hospital_ids()
    if filters.hospital_ids:
        wanted = set(filters.hospital_ids)
        return [h for h in all_ids if h in wanted]
    return all_ids


def _merge_overview(group_by: GroupBy, filters: GroupFilters,
                     per_tenant_results: list[dict | list[dict]]) -> dict:
    rows: list[dict] = []
    for r in per_tenant_results:
        if r is None:
            continue
        if isinstance(r, list):
            rows.extend(r)
        elif "error" in r:
            rows.append(r)
        else:
            rows.append(r)
    ok_rows = [row for row in rows if "error" not in row]
    totals = {
        "total_people": sum(row.get("total_people", 0) for row in ok_rows),
        "red_count": sum(row.get("red_count", 0) for row in ok_rows),
        "yellow_count": sum(row.get("yellow_count", 0) for row in ok_rows),
        "green_count": sum(row.get("green_count", 0) for row in ok_rows),
    }
    tp = totals["total_people"]
    totals["abnormal_rate"] = round(
        (totals["red_count"] + totals["yellow_count"]) / tp, 4
    ) if tp > 0 else 0.0
    return {
        "group_by": group_by,
        "filters": filters.model_dump(mode="json"),
        "rows": rows,
        "totals": totals,
    }


def get_overview(group_by: GroupBy, filters: GroupFilters) -> dict:
    import time
    t0 = time.time()
    hids = _resolve_tenants(filters)
    names = _get_tenant_names(hids)
    items = [(h, names.get(h, h)) for h in hids]
    results: list = []
    if not items:
        return _merge_overview(group_by, filters, [])
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as tp:
        futs = [tp.submit(_per_tenant_overview, h, name, group_by, filters)
                for h, name in items]
        for f in futs:
            results.append(f.result())
    out = _merge_overview(group_by, filters, results)
    err_n = sum(1 for r in results if isinstance(r, dict) and "error" in r)
    logger.info("group_overview group_by=%s took=%.2fs rows=%d errors=%d",
                group_by, time.time() - t0, len(out["rows"]), err_n)
    return out
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_service.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/statistics/group_service.py \
        backend/tests/modules/statistics/test_group_service.py
git commit -m "feat(statistics): get_overview cross-tenant orchestration + totals merge"
```

---

## Task 6: 高风险清单 `get_high_risk` —— 跨库分页 + 排序 + 50000 CSV 上限

**Files:**
- Modify: `backend/app/modules/statistics/group_service.py`
- Modify: `backend/tests/modules/statistics/test_group_service.py`

**Interfaces:**
- Produces:
  - `def get_high_risk(filters: GroupFilters, sort: str, page: int, page_size: int) -> dict`
    - 返回 `HighRiskResponse`-style dict
    - 跨库实现"每库先 count,合成总 total,再分页":简化版用每库拿 `page_size` 行,merge 全部后再按 sort 排序截取对应 offset/page_size 切片(数据量本期可接受,后续真上量再优化为 cursor)
    - 总 total = sum 各库 count

- [ ] **Step 1: 写失败测试**

```python
from app.modules.statistics.group_service import get_high_risk


def test_get_high_risk_basic_total_and_pagination(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service._resolve_tenants",
                         lambda f: ["H001"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "医院A"})
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_count",
                         lambda hid, f: 233)
    from datetime import date
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_rows",
                         lambda hid, name, f, s, lim, off:
                         [{"hospital_id": hid, "hospital_name": name,
                           "report_id": i, "user_id": i * 10, "name": f"u{i}",
                           "gender": "M", "age": 40,
                           "report_date": date(2026, 1, 1),
                           "batch_id": None, "batch_name": None,
                           "overall_level": "red", "red_count": 1, "yellow_count": 0,
                           "summary_text": "s"} for i in range(20)])
    r = get_high_risk(GroupFilters(), sort="red_count", page=1, page_size=20)
    assert r["total"] == 233
    assert r["page"] == 1 and r["page_size"] == 20
    assert len(r["items"]) == 20
    assert r["items"][0]["hospital_id"] == "H001"
    assert r["items"][0]["hospital_name"] == "医院A"
    assert r["items"][0]["red_count"] == 1


def test_get_high_risk_filter_unknown_hospital_id_dropped(monkeypatch):
    # _resolve_tenants 把 hospital_ids 与 get_all_hospital_ids() 取交集;
    # mock get_all_hospital_ids 只返回 ["H002"],则 filters.hospital_ids=["H001","H999"]
    # 交集为空 -> rows=[] total=0
    monkeypatch.setattr("app.modules.statistics.group_service.get_all_hospital_ids",
                         lambda: ["H002"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {})
    counts = {}
    def fake_count(hid, f): counts[hid] = counts.get(hid, 0) + 1; return 0
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_count",
                         fake_count)
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_rows",
                         lambda *a, **k: [])
    r = get_high_risk(GroupFilters(hospital_ids=["H001", "H999"]),
                      sort="red_count", page=1, page_size=20)
    assert r["total"] == 0 and r["items"] == []
    assert counts == {}  # 因 tenant 列表为空,_per_tenant_* 一次都没被调


def test_get_high_risk_pagination_slices_correctly(monkeypatch):
    """跨库候选 50 条(sort_key=red_count 全=5),page_size=20 -> 第 1 页 20、第 3 页 10。"""
    monkeypatch.setattr("app.modules.statistics.group_service._resolve_tenants",
                         lambda f: ["H001"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "A"})
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_count",
                         lambda hid, f: 50)
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_rows",
                         lambda hid, name, f, s, lim, off:
                         [{"hospital_id": hid, "hospital_name": name,
                           "report_id": i, "user_id": i, "name": f"u{i}",
                           "gender": "M", "age": 40, "report_date": None,
                           "batch_id": None, "batch_name": None,
                           "overall_level": "red", "red_count": 5, "yellow_count": 0,
                           "summary_text": ""} for i in range(50)])
    p1 = get_high_risk(GroupFilters(), sort="red_count", page=1, page_size=20)
    p3 = get_high_risk(GroupFilters(), sort="red_count", page=3, page_size=20)
    assert len(p1["items"]) == 20
    assert len(p3["items"]) == 10
```

实现者注意:`get_high_risk` 跨库分页的简化策略 —— 每库单独拿全部(但 limit 50000 上限内)、Python 层合 sort 后做 offset/limit 切片。CSV 导出同源但绕过分页用全量。

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_service.py::test_get_high_risk_basic_total_and_pagination -v`
Expected: ImportError

- [ ] **Step 3: 实现(追加到 group_service.py)**

```python
HIGH_RISK_CSV_MAX = 50_000


def _per_tenant_high_risk_rows(hid: str, hname: str, filters: GroupFilters,
                                 sort: str, limit: int, offset: int) -> list[dict]:
    db = get_session(f"hospital_{hid}")
    try:
        sql, params = build_high_risk_list_sql(filters, sort, limit, offset)
        rows = db.execute(text(sql), params).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append({
                "hospital_id": hid, "hospital_name": hname,
                "report_id": r.report_id, "user_id": r.user_id,
                "name": r.name, "gender": r.gender, "age": r.age,
                "report_date": r.report_date,
                "batch_id": r.batch_id, "batch_name": r.batch_name,
                "overall_level": r.overall_level,
                "red_count": r.red_count, "yellow_count": r.yellow_count,
                "summary_text": r.summary_text,
            })
        return out
    except Exception:
        logger.exception("group_high_risk_rows hid=%s failed", hid)
        return []
    finally:
        db.close()


def _per_tenant_high_risk_count(hid: str, filters: GroupFilters) -> int:
    db = get_session(f"hospital_{hid}")
    try:
        sql, params = build_high_risk_list_sql(filters, "red_count",
                                                limit=0, offset=0, count_only=True)
        row = db.execute(text(sql), params).fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:
        logger.exception("group_high_risk_count hid=%s failed", hid)
        return 0
    finally:
        db.close()


def get_high_risk(filters: GroupFilters, sort: str,
                   page: int, page_size: int) -> dict:
    hids = _resolve_tenants(filters)
    names = _get_tenant_names(hids)
    candidate_rows: list[dict] = []
    totals_per_h: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as tp:
        cnt_futs = {tp.submit(_per_tenant_high_risk_count, h, filters): h for h in hids}
        for f in cnt_futs:
            h = cnt_futs[f]
            totals_per_h[h] = f.result()
        row_futs = {tp.submit(_per_tenant_high_risk_rows, h, names.get(h, h),
                                filters, sort, HIGH_RISK_CSV_MAX, 0): h for h in hids}
        for f in row_futs:
            candidate_rows.extend(f.result())
    total = sum(totals_per_h.values())
    sort_key = {"red_count": "red_count", "age": "age",
                "report_date": "report_date"}.get(sort, "red_count")
    candidate_rows.sort(key=lambda x: (x.get(sort_key) or 0), reverse=True)
    start = (page - 1) * page_size
    end = start + page_size
    items = candidate_rows[start:end]
    return {
        "items": items, "total": total,
        "page": page, "page_size": page_size,
        "filters": filters.model_dump(mode="json"),
    }
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_service.py -v`
Expected: all passed(原 6 + 新 2 + 总)

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/statistics/group_service.py \
        backend/tests/modules/statistics/test_group_service.py
git commit -m "feat(statistics): get_high_risk cross-tenant pagination + sort"
```

---

## Task 7: CSV streaming 导出

**Files:**
- Modify: `backend/app/modules/statistics/group_service.py`
- Modify: `backend/tests/modules/statistics/test_group_service.py`

**Interfaces:**
- Produces:
  - `def stream_high_risk_csv(filters: GroupFilters, sort: str) -> Iterable[bytes]`
    - 先 `total = sum _per_tenant_high_risk_count`,超 50000 抛 `HTTPException(413, ...)`
    - 否则 yield UTF-8 BOM `'\ufeff'.encode('utf-8')` + CSV 表头 + 各行(跨库 candidate rows 排序后流式输出)
    - 使用 stdlib `csv` + `io.StringIO`,每批量(每库)fushed to bytes

- [ ] **Step 1: 写失败测试**

```python
import csv
import io
from app.modules.statistics.group_service import stream_high_risk_csv
from fastapi import HTTPException


def test_stream_high_risk_csv_basic(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service._resolve_tenants",
                         lambda f: ["H001"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "医院"})
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_count",
                         lambda hid, f: 2)
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_rows",
                         lambda hid, name, f, s, lim, off:
                         [{"hospital_id": hid, "hospital_name": name,
                           "report_id": 1, "user_id": 10, "name": "张三",
                           "gender": "M", "age": 40,
                           "report_date": __import__("datetime").date(2026,1,1),
                           "batch_id": None, "batch_name": None,
                           "overall_level": "red", "red_count": 5,
                           "yellow_count": 0, "summary_text": "s"},
                          {"hospital_id": hid, "hospital_name": name,
                           "report_id": 2, "user_id": 20, "name": "李四",
                           "gender": "F", "age": 30,
                           "report_date": __import__("datetime").date(2026,2,1),
                           "batch_id": None, "batch_name": None,
                           "overall_level": "yellow", "red_count": 3,
                           "yellow_count": 1, "summary_text": "s2"}])
    chunks = list(stream_high_risk_csv(GroupFilters(), sort="red_count"))
    full = b"".join(chunks)
    assert full.startswith("\ufeff".encode("utf-8"))
    text_dec = full.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text_dec))
    rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["hospital_id"] == "H001"
    assert "report_id" in rows[0] and "red_count" in rows[0]


def test_stream_high_risk_csv_over_limit_raises_413(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service._resolve_tenants",
                         lambda f: ["H001", "H002"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "A", "H002": "B"})
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_count",
                         lambda hid, f: 30_000)  # 2 * 30000 = 60000 > 50000
    try:
        list(stream_high_risk_csv(GroupFilters(), sort="red_count"))
        assert False, "expected HTTPException 413"
    except HTTPException as e:
        assert e.status_code == 413
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_service.py -v -k csv`
Expected: ImportError on `stream_high_risk_csv`

- [ ] **Step 3: 实现**

```python
import csv
import io
from typing import Iterable
from fastapi import HTTPException

CSV_COLUMNS = [
    "hospital_id", "hospital_name", "report_id", "user_id", "name",
    "gender", "age", "report_date", "batch_id", "batch_name",
    "overall_level", "red_count", "yellow_count", "summary_text",
]


def stream_high_risk_csv(filters: GroupFilters, sort: str) -> Iterable[bytes]:
    hids = _resolve_tenants(filters)
    names = _get_tenant_names(hids)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as tp:
        cnt_futs = [tp.submit(_per_tenant_high_risk_count, h, filters) for h in hids]
        total = sum(f.result() for f in cnt_futs)
    if total > HIGH_RISK_CSV_MAX:
        raise HTTPException(status_code=413, detail="high-risk export exceeds 50000 rows, please narrow filters")
    # 流式:BOM + header + 每库 rows
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    yield buf.getvalue().encode("utf-8")
    # 跨库排序简化:每库返回 schema-row dicts,单库内部排好 sort desc;
    # 跨库不再全局排序(导出大文件场景下,线性 merge 替代全局 sort 性能可接受,本期不严格全局排序 —— 与 json 分页略差别)
    # 严格全局排序要求把全部数据加载内存,50000 上限行可承受 —— 但 stream 优势丢失。本期方案:接受按 hospital + 单库 sort 排。
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as tp:
        row_futs = [tp.submit(_per_tenant_high_risk_rows, h, names.get(h, h),
                                filters, sort, HIGH_RISK_CSV_MAX, 0) for h in hids]
        for f in row_futs:
            rows = f.result()
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            for row in rows:
                # date 转 ISO string
                rd = row.get("report_date")
                if hasattr(rd, "isoformat"):
                    row = {**row, "report_date": rd.isoformat()}
                writer.writerow(row)
            yield buf.getvalue().encode("utf-8")
    logger.info("group_high_risk_csv rows=%d", total)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_service.py -v -k csv`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/statistics/group_service.py \
        backend/tests/modules/statistics/test_group_service.py
git commit -m "feat(statistics): high-risk CSV streaming + 50k guard"
```

---

## Task 8: group_router.py + main.py wiring + 鉴权集成测试

**Files:**
- Create: `backend/app/modules/statistics/group_router.py`
- Modify: `backend/app/main.py`(include 新 router)
- Create: `backend/tests/modules/statistics/test_group_router.py`

**Interfaces:**
- Consumes:
  - `app.core.dependencies.require_role`(`dependencies.py:39-43`)
  - `group_service.get_overview` / `get_high_risk` / `stream_high_risk_csv`
  - `group_schemas.parse_csv_query` / pydantic 模型 / `GroupBy` Literal
- Produces:
  - `router = APIRouter()`,2 个 endpoint:
    - `GET /group/overview`
    - `GET /group/high-risk`
  - query date 转换:`date_from: date | None = Query(None)`;`hospital_ids: str | None = Query(None)` 然后 `parse_csv_query`

- [ ] **Step 1: 写失败测试**

`backend/tests/modules/statistics/test_group_router.py`:
```python
from datetime import date
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import app.main as main_mod
from app.core.dependencies import get_current_user, CurrentUser


def test_group_routes_registered():
    paths = {getattr(r, "path", None) for r in main_mod.app.routes}
    assert "/api/v1/statistics/group/overview" in paths
    assert "/api/v1/statistics/group/high-risk" in paths


def test_overview_admin_only_non_admin_forbidden():
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="doctor", hospital_id=None)
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/statistics/group/overview?group_by=hospital")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_overview_admin_returns_200(monkeypatch):
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="admin", hospital_id=None)
    fake = {"group_by": "hospital", "filters": {}, "rows": [], "totals": {}}
    monkeypatch.setattr("app.modules.statistics.group_router.get_overview",
                         lambda gb, f: fake)
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/statistics/group/overview?group_by=hospital")
        assert r.status_code == 200
        assert r.json()["group_by"] == "hospital"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_overview_invalid_group_by_returns_422():
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="admin", hospital_id=None)
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/statistics/group/overview?group_by=foo")
        assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_high_risk_csv_endpoint_returns_blob(monkeypatch):
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="admin", hospital_id=None)
    def fake_stream(f, s):
        yield "\ufeffhospital_id\nH001\n".encode("utf-8")
    monkeypatch.setattr("app.modules.statistics.group_router.stream_high_risk_csv",
                         fake_stream)
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/statistics/group/high-risk?format=csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert r.content.startswith("\ufeff".encode("utf-8"))
    finally:
        app.dependency_overrides.pop(get_current_user, None)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_router.py -v`
Expected: routes 未 registered → fail

- [ ] **Step 3: 实现 group_router.py**

`backend/app/modules/statistics/group_router.py`:
```python
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.dependencies import require_role
from app.modules.statistics.group_schemas import (
    GroupBy, GroupFilters, GroupBy as _Gb, ExportFormat, SortKey,
    parse_csv_query,
)
from app.modules.statistics.group_service import (
    get_overview, get_high_risk, stream_high_risk_csv,
)

router = APIRouter()


def _filters(
    hospital_ids: Optional[str] = Query(None),
    batch_ids: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    gender: Optional[str] = Query(None),
    age_groups: Optional[str] = Query(None),
    topn: int = Query(10, ge=1, le=100),
) -> GroupFilters:
    return GroupFilters(
        hospital_ids=parse_csv_query(hospital_ids),
        batch_ids=parse_csv_query(batch_ids),
        date_from=date_from,
        date_to=date_to,
        gender=gender,
        age_groups=parse_csv_query(age_groups),
        topn=topn,
    )


@router.get("/group/overview")
def group_overview(
    group_by: GroupBy = Query(...),
    _admin: None = Depends(require_role("admin")),
    filters: GroupFilters = Depends(_filters),
):
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        from app.utils.exceptions import ValidationException
        raise ValidationException(detail="date_from must be <= date_to")
    return get_overview(group_by, filters)


@router.get("/group/high-risk")
def group_high_risk(
    _admin: None = Depends(require_role("admin")),
    filters: GroupFilters = Depends(_filters),
    sort: SortKey = Query("red_count"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    format: ExportFormat = Query("json"),
):
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        from app.utils.exceptions import ValidationException
        raise ValidationException(detail="date_from must be <= date_to")
    if format == "csv":
        return StreamingResponse(
            stream_high_risk_csv(filters, sort),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=high-risk.csv"},
        )
    return get_high_risk(filters, sort, page, page_size)
```

- [ ] **Step 4: 在 main.py 注册**

`backend/app/main.py` modify —— 在 `from app.modules.statistics.router import router as statistics_router`(line 17)之后加一行,并在 include 处增加。

```python
from app.modules.statistics.router import router as statistics_router
from app.modules.statistics.group_router import router as statistics_group_router  # 新增
```

并在 `app.include_router(statistics_router, prefix="/api/v1/statistics", tags=["statistics"])` 之后增加:
```python
app.include_router(statistics_group_router, prefix="/api/v1/statistics", tags=["statistics"])
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd backend && .venv/bin/pytest tests/modules/statistics/test_group_router.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/statistics/group_router.py \
        backend/app/main.py \
        backend/tests/modules/statistics/test_group_router.py
git commit -m "feat(statistics): group_router endpoints + main.wiring"
```

---

## Task 9: 前端 - 安装依赖 + vite 代理 + api 客户端 + 路由

**Files:**
- Modify: `frontend/packages/admin-portal/package.json`(加 echarts/echarts-for-react)
- Modify: `frontend/packages/admin-portal/vite.config.ts`(加 proxy)
- Create: `frontend/packages/admin-portal/src/api/groupAnalysis.ts`
- Modify: `frontend/packages/admin-portal/src/router.tsx`(加路由)

**Interfaces:**
- Produces:
  - `getOverview(params)` / `getHighRisk(params)` / `downloadHighRiskCsv(params)` 三个 HTTP 调用函数
  - `/group-analysis` 路由(指向 GroupAnalysisPage)

- [ ] **Step 1: 安装依赖**

Run:
```bash
cd frontend && pnpm -F @hospital/admin-portal add echarts echarts-for-react
```
Expected: package.json 增加 dependencies;node_modules 装好。

- [ ] **Step 2: 更新 vite.config.ts**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3003,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
```

- [ ] **Step 3: 写 api/groupAnalysis.ts**

`frontend/packages/admin-portal/src/api/groupAnalysis.ts`:
```ts
import { useAdminStore } from "@/stores/adminStore";

const api = () => useAdminStore.getState().api;

export interface OverviewRow {
  key: string;
  label: string;
  total_people: number;
  red_count: number;
  yellow_count: number;
  green_count: number;
  abnormal_rate: number;
  by_gender?: { key: string; count: number }[];
  by_age_group?: { key: string; count: number }[];
  top_abnormal_items?: { item: string; red_count: number }[];
  error?: string;
}

export interface OverviewResponse {
  group_by: string;
  filters: Record<string, any>;
  rows: OverviewRow[];
  totals: Record<string, number>;
}

export interface HighRiskItem {
  hospital_id: string;
  hospital_name: string;
  report_id: number;
  user_id: number;
  name?: string;
  gender?: string;
  age?: number;
  report_date?: string;
  batch_id?: string;
  batch_name?: string;
  overall_level?: string;
  red_count: number;
  yellow_count: number;
  summary_text?: string;
}

export interface HighRiskResponse {
  items: HighRiskItem[];
  total: number;
  page: number;
  page_size: number;
  filters: Record<string, any>;
}

export type GroupBy = "hospital" | "batch" | "age_group" | "gender" | "time_month";

export async function getOverview(params: Record<string, any>): Promise<OverviewResponse> {
  const r = await api().get("/statistics/group/overview", { params });
  return r.data;
}

export async function getHighRisk(params: Record<string, any>): Promise<HighRiskResponse> {
  const r = await api().get("/statistics/group/high-risk", { params });
  return r.data;
}

export async function downloadHighRiskCsv(params: Record<string, any>): Promise<void> {
  const r = await api().get("/statistics/group/high-risk", {
    params: { ...params, format: "csv" },
    responseType: "blob",
  });
  const url = URL.createObjectURL(r.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = "high-risk.csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
```

- [ ] **Step 4: 改 router.tsx**

```tsx
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAdminStore } from './stores/adminStore';
import LoginPage from './pages/LoginPage';
import PlatformDashboard from './pages/PlatformDashboard';
import GroupAnalysisPage from './pages/group-analysis/GroupAnalysisPage';

function AuthGuard({ children }: { children: React.ReactNode }) {
  if (!useAdminStore(s => s.token)) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export const AppRouter = () => (
  <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/" element={<AuthGuard><PlatformDashboard /></AuthGuard>} />
    <Route path="/group-analysis" element={<AuthGuard><GroupAnalysisPage /></AuthGuard>} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
);
```

(Step 4 will fail to compile until GroupAnalysisPage exists — leave placeholder file:

```tsx
// frontend/packages/admin-portal/src/pages/group-analysis/GroupAnalysisPage.tsx
export default function GroupAnalysisPage() {
  return <div>Group Analysis (TODO Task 10)</div>;
}
```
)

- [ ] **Step 5: 验证编译**

Run: `cd frontend && pnpm -F @hospital/admin-portal build`
Expected: build 成功(临时 page 占位 OK,稍后 Task 10 替换)

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/admin-portal/package.json frontend/package-lock.json \
        frontend/packages/admin-portal/vite.config.ts \
        frontend/packages/admin-portal/src/api/groupAnalysis.ts \
        frontend/packages/admin-portal/src/router.tsx \
        frontend/packages/admin-portal/src/pages/group-analysis/GroupAnalysisPage.tsx
git commit -m "feat(admin-portal): group-analysis scaffold (deps/proxy/api/router stub)"
```

---

## Task 10: 前端 - 实现 GroupAnalysisPage 完整 UI(FilterBar/OverviewCharts/HighRiskTable)

**Files:**
- Modify: `frontend/packages/admin-portal/src/pages/group-analysis/GroupAnalysisPage.tsx`(替换占位)
- Create: `frontend/packages/admin-portal/src/pages/group-analysis/components/FilterBar.tsx`
- Create: `frontend/packages/admin-portal/src/pages/group-analysis/components/OverviewCharts.tsx`
- Create: `frontend/packages/admin-portal/src/pages/group-analysis/components/HighRiskTable.tsx`

**Interfaces:**
- props 链:
  - `GroupAnalysisPage` 顶部:Tabs ["概览图", "重点人群"]。state:`filters`(GroupFilters form)、`group_by`、tableData
  - `FilterBar`(props: `value: FiltersState`, `onChange: (next) => void`, `onSubmit: () => void`, `groupBy: GroupBy`, `onGroupByChange: (g: GroupBy) => void`)。
    - 医院:`Select mode=multiple` 拉 `/tenants` list 暂用 mock(本任务可 hardcode 取 `api.get("/tenants")` 或省略且默认无 filter;若 `/tenants` list endpoint 不存在则按 AGENTS.md 留手动输入) —— 简化:**医院过滤为 Select multiple,选项标"加载中"且接受手工输入 tag**(无 GET /tenants)。后续单独 task 加 endpoint。
    - 批次:`Input mode tags` 接受 uuid 列表(纯文本)。
    - 日期范围:`DatePicker RangePicker`。
    - 性别:`RadioGroup M/F/全部`。
    - 年龄段:`Select multiple`(options 写死 6 段)。
    - `group_by`:`Select`(5 个维度的中文 label)。
    - "查询" 按钮。
  - `OverviewCharts`(props: `data: OverviewResponse | null`, `loading: boolean`, `groupBy: GroupBy`):
    - 按 group_by 渲染不同 ECharts option(用 ReactECharts):
      - hospital:三色堆叠柱(X 轴 rows.map(label)) + 异常率折线(双 Y 轴)+ Top10 横向条(单图)。
      - batch:复用 hospital 模板,X 轴 batch_name。
      - age_group:折线(X 轴 key,Y 异常率)+ 人数饼。
      - gender:三色堆叠柱(2 根:M,F)。
      - time_month:折线(X 轴 key,Y 异常率)。
    - row 含 `error:"db_unavailable"` 时该 bar 橙色 + tooltip 提示。
  - `HighRiskTable`(props: `filters: FiltersState`):
    - AntD `Table`,分页调 `getHighRisk`(`/statistics/group/high-risk`),server-side 分页。
    - 默认 sort `red_count desc`;Columns:医院、姓名、性别、年龄、体检日期、整体级别、红指标数、黄指标数、解读摘要。
    - 右上"导出 CSV" 按钮调 `downloadHighRiskCsv(filters)`。
- 数据流:FilterBar submit → GroupAnalysisPage 更新 `effectiveFilters` state → 概览 tab 调 getOverview;高风险 tab mount 自动调 getHighRisk(第 1 页)。

- [ ] **Step 1: 写组件 FilterBar.tsx**

```tsx
import { Form, Select, DatePicker, Radio, Input, Button } from "antd";
import type { GroupBy } from "../../../api/groupAnalysis";

const { RangePicker } = DatePicker;

const GROUP_BY_OPTIONS = [
  { value: "hospital", label: "医院" },
  { value: "batch", label: "批次" },
  { value: "age_group", label: "年龄段" },
  { value: "gender", label: "性别" },
  { value: "time_month", label: "时间(月)" },
];

const AGE_GROUP_OPTIONS = [
  "<20", "20-29", "30-39", "40-49", "50-59", "60+",
].map(v => ({ value: v, label: v }));

export interface FiltersState {
  hospital_ids?: string[];
  batch_ids?: string[];
  date_from?: string;
  date_to?: string;
  gender?: string;
  age_groups?: string[];
}

interface Props {
  value: FiltersState;
  onChange: (next: FiltersState) => void;
  onSubmit: () => void;
  groupBy: GroupBy;
  onGroupByChange: (g: GroupBy) => void;
}

export default function FilterBar({ value, onChange, onSubmit, groupBy, onGroupByChange }: Props) {
  return (
    <Form layout="inline" style={{ marginBottom: 16 }}>
      <Form.Item label="分组维度">
        <Select value={groupBy} onChange={v => onGroupByChange(v as GroupBy)}
          options={GROUP_BY_OPTIONS} style={{ width: 120 }} />
      </Form.Item>
      <Form.Item label="医院">
        <Select mode="tags" placeholder="留空=全部"
          value={value.hospital_ids || []}
          onChange={v => onChange({ ...value, hospital_ids: v as string[] })}
          style={{ minWidth: 200 }} />
      </Form.Item>
      <Form.Item label="批次UUID">
        <Input placeholder="逗号或回车分隔" style={{ width: 220 }}
          value={(value.batch_ids || []).join(",")}
          onChange={e => onChange({
            ...value,
            batch_ids: e.target.value.split(",").map(s => s.trim()).filter(Boolean),
          })} />
      </Form.Item>
      <Form.Item label="日期范围">
        <RangePicker
          value={value.date_from && value.date_to ? [
            // @ts-ignore simplify
            dayjs(value.date_from), dayjs(value.date_to)
          ] : undefined}
          onChange={(_, ds) => onChange({
            ...value,
            date_from: ds[0] as string || undefined,
            date_to: ds[1] as string || undefined,
          })} />
      </Form.Item>
      <Form.Item label="性别">
        <Radio.Group value={value.gender || ""}
          onChange={e => onChange({ ...value, gender: e.target.value || undefined })}>
          <Radio value="">全部</Radio>
          <Radio value="M">男</Radio>
          <Radio value="F">女</Radio>
        </Radio.Group>
      </Form.Item>
      <Form.Item label="年龄段">
        <Select mode="multiple" placeholder="留空=全部"
          value={value.age_groups || []}
          onChange={v => onChange({ ...value, age_groups: v as string[] })}
          options={AGE_GROUP_OPTIONS} style={{ minWidth: 200 }} />
      </Form.Item>
      <Form.Item>
        <Button type="primary" onClick={onSubmit}>查询</Button>
      </Form.Item>
    </Form>
  );
}
```

注:实现者需 `import dayjs from "dayjs"` 并在 admin-portal 装 `dayjs`(antd 5 已透传,无需新增依赖;若编译报错则 `pnpm -F @hospital/admin-portal add dayjs`)。

- [ ] **Step 2: 写 OverviewCharts.tsx**

```tsx
import ReactECharts from "echarts-for-react";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { Card, Spin } from "antd";
import type { OverviewResponse, GroupBy } from "../../../api/groupAnalysis";

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent,
  LegendComponent, TitleComponent, CanvasRenderer]);

interface Props {
  data: OverviewResponse | null;
  loading: boolean;
  groupBy: GroupBy;
}

export default function OverviewCharts({ data, loading, groupBy }: Props) {
  if (loading || !data) return <Spin />;
  const labels = data.rows.map(r => r.label);
  const reds = data.rows.map(r => r.red_count);
  const yellows = data.rows.map(r => r.yellow_count);
  const greens = data.rows.map(r => r.green_count);
  const rates = data.rows.map(r => Number((r.abnormal_rate * 100).toFixed(1)));

  let option: any;
  if (groupBy === "hospital" || groupBy === "batch") {
    option = {
      tooltip: { trigger: "axis" },
      legend: { data: ["红", "黄", "绿", "异常率%"] },
      xAxis: { type: "category", data: labels },
      yAxis: [
        { type: "value", name: "人数" },
        { type: "value", name: "异常率%", max: 100 },
      ],
      series: [
        { name: "红", type: "bar", stack: "t", data: reds, itemStyle: { color: "#ff4d4f" } },
        { name: "黄", type: "bar", stack: "t", data: yellows, itemStyle: { color: "#faad14" } },
        { name: "绿", type: "bar", stack: "t", data: greens, itemStyle: { color: "#52c41a" } },
        { name: "异常率%", type: "line", yAxisIndex: 1, data: rates,
          itemStyle: { color: "#1890ff" } },
      ],
    };
  } else if (groupBy === "age_group" || groupBy === "time_month") {
    option = {
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: labels },
      yAxis: { type: "value", name: "异常率%", max: 100 },
      series: [{ type: "line", data: rates, smooth: true,
        itemStyle: { color: "#1890ff" } }],
    };
  } else if (groupBy === "gender") {
    option = {
      tooltip: { trigger: "axis" },
      legend: { data: ["红", "黄", "绿"] },
      xAxis: { type: "category", data: labels },
      yAxis: { type: "value" },
      series: [
        { name: "红", type: "bar", stack: "t", data: reds, itemStyle: { color: "#ff4d4f" } },
        { name: "黄", type: "bar", stack: "t", data: yellows, itemStyle: { color: "#faad14" } },
        { name: "绿", type: "bar", stack: "t", data: greens, itemStyle: { color: "#52c41a" } },
      ],
    };
  } else {
    option = {};
  }

  const topItems = groupBy === "hospital" || groupBy === "batch"
    ? data.rows.flatMap(r => r.top_abnormal_items || []).slice(0, 10)
    : [];

  return (
    <div>
      <Card title="团体健康体检分析">
        <ReactECharts echarts={echarts} option={option} style={{ height: 320 }} />
      </Card>
      {topItems.length > 0 && (
        <Card title="Top 异常指标" style={{ marginTop: 16 }}>
          <ReactECharts echarts={echarts} option={{
            tooltip: { trigger: "axis" },
            xAxis: { type: "value" },
            yAxis: { type: "category", data: topItems.map(t => t.item) },
            series: [{ type: "bar", data: topItems.map(t => t.red_count),
              itemStyle: { color: "#ff4d4f" } }],
          }} style={{ height: 240 }} />
        </Card>
      )}
    </div>
  );
}
```

- [ ] **Step 3: 写 HighRiskTable.tsx**

```tsx
import { useEffect, useState } from "react";
import { Table, Button } from "antd";
import type { ColumnsType } from "antd/es/table";
import { getHighRisk, downloadHighRiskCsv, HighRiskItem } from "../../../api/groupAnalysis";
import type { FiltersState } from "./FilterBar";

interface Props {
  filters: FiltersState;
}

export default function HighRiskTable({ filters }: Props) {
  const [data, setData] = useState<HighRiskItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [loading, setLoading] = useState(false);

  const load = async (p: number, ps: number) => {
    setLoading(true);
    try {
      const r = await getHighRisk({
        ...filters,
        page: p,
        page_size: ps,
        sort: "red_count",
      });
      setData(r.items);
      setTotal(r.total);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(1, 20); }, [JSON.stringify(filters)]);
  // 注意 filters 是对象,JSON.stringify 当 deps 比较键,避免每次 render 重复 fetch

  const columns: ColumnsType<HighRiskItem> = [
    { title: "医院", dataIndex: "hospital_name", width: 140 },
    { title: "姓名", dataIndex: "name", width: 80 },
    { title: "性别", dataIndex: "gender", width: 60 },
    { title: "年龄", dataIndex: "age", width: 60 },
    { title: "体检日期", dataIndex: "report_date", width: 110 },
    { title: "整体级别", dataIndex: "overall_level", width: 80 },
    { title: "红指标数", dataIndex: "red_count", width: 80, sorter: true,
      defaultSortOrder: "descend" },
    { title: "黄指标数", dataIndex: "yellow_count", width: 80 },
    { title: "解读摘要", dataIndex: "summary_text", ellipsis: true },
  ];

  return (
    <div>
      <div style={{ marginBottom: 8, textAlign: "right" }}>
        <Button onClick={() => downloadHighRiskCsv({ ...filters, sort: "red_count" })}>
          导出 CSV
        </Button>
      </div>
      <Table
        rowKey={r => `${r.hospital_id}-${r.report_id}`}
        columns={columns}
        dataSource={data}
        loading={loading}
        pagination={{
          current: page, pageSize, total,
          onChange: (p, ps) => { setPage(p); setPageSize(ps); load(p, ps); },
          showSizeChanger: true,
        }}
      />
    </div>
  );
}
```

- [ ] **Step 4: 写 GroupAnalysisPage.tsx(替换 Task 9 占位)**

```tsx
import { useState } from "react";
import { Tabs } from "antd";
import FilterBar, { FiltersState } from "./components/FilterBar";
import OverviewCharts from "./components/OverviewCharts";
import HighRiskTable from "./components/HighRiskTable";
import { getOverview, OverviewResponse, GroupBy } from "../../api/groupAnalysis";

export default function GroupAnalysisPage() {
  const [filters, setFilters] = useState<FiltersState>({});
  const [effective, setEffective] = useState<FiltersState>({});
  const [groupBy, setGroupBy] = useState<GroupBy>("hospital");
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setEffective(filters);
    setLoading(true);
    try {
      const r = await getOverview({ group_by: groupBy, ...filters });
      setOverview(r);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <h2>团体健康体检分析</h2>
      <FilterBar value={filters} onChange={setFilters} onSubmit={submit}
        groupBy={groupBy} onGroupByChange={setGroupBy} />
      <Tabs items={[
        {
          key: "overview",
          label: "概览图",
          children: <OverviewCharts data={overview} loading={loading} groupBy={groupBy} />,
        },
        {
          key: "high-risk",
          label: "重点人群",
          children: <HighRiskTable filters={effective} />,
        },
      ]} />
    </div>
  );
}
```

- [ ] **Step 5: 编译验证**

Run: `cd frontend && pnpm -F @hospital/admin-portal build`
Expected: TypeScript 编译通过(ECharts 5 + antd 5 + React 18 兼容)。如有 TS error 按提示修正类型注解。

- [ ] **Step 6: 手动验收**

启动 backend + admin-portal dev,登录 admin 账号,访问 `/group-analysis`:
- 选 group_by=hospital 点查询,看到各院三色堆叠柱 + 异常率折线 + Top10 异常指标。
- 切到"重点人群" tab,看到分页表格与"导出 CSV"按钮,点查表翻页正常。
- 试着筛 single hospital、按 gender、按年龄段。

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/admin-portal/src/pages/group-analysis/
git commit -m "feat(admin-portal): group-analysis page (filters + charts + high-risk table + CSV)"
```

---

## 验收(全部完成后整体验证)

后端:
```bash
cd backend && .venv/bin/pytest tests/modules/statistics/ -v
# 期望:全部 passed(schemas 6 + sql 18 + service ~12 + router 5)
```

端到端 curl(先 /auth/login 拿 admin JWT):
```bash
JWT=$(curl -sX POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"..."}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s -H "Authorization: Bearer $JWT" \
  'http://localhost:8000/api/v1/statistics/group/overview?group_by=hospital' | head
# 期望:{"group_by":"hospital","rows":[...],"totals":{...}}

curl -s -H "Authorization: Bearer $JWT" \
  'http://localhost:8000/api/v1/statistics/group/high-risk?page=1&page_size=5' | head
# 期望:{"items":[...],"total":N,"page":1,"page_size":5,...}

curl -s -H "Authorization: Bearer $JWT" -o /tmp/high-risk.csv \
  'http://localhost:8000/api/v1/statistics/group/high-risk?format=csv'
file /tmp/high-risk.csv  # 期望:UTF-8 Unicode (with BOM) text
```

前端:
```bash
cd frontend && pnpm -F @hospital/admin-portal dev
# 浏览器 http://localhost:3003/group-analysis
```

## Spec 覆盖检查清单

| Spec 节 | 覆盖 Task |
|---|---|
| §1 背景与动机 | (design only) |
| §2 现状关键事实 | (research only) |
| §3 决策表 | Task 1-8 各处 |
| §4 数据流 | Task 4-5-8 |
| §5.1 Overview API | Task 2, 4, 5, 8 |
| §5.2 HighRisk API | Task 3, 6, 7, 8 |
| §5.3 错误表 | Task 8(401/403/422/400/413) |
| §6.1 文件结构 | Task 1-8 |
| §6.2 鉴权依赖 | Task 8 |
| §6.3 SQL 组合 | Task 2, 3 |
| §6.4 service 主函数 | Task 4, 5, 6, 7 |
| §6.5 logger 命名 | Task 5(`app.statistics`) |
| §7 前端 | Task 9, 10 |
| §8 错误处理边界 | Task 4, 5, 7, 8 |
| §9 测试 | Task 1-7 全部 |
| §10 本期不做 | (none) |
| §11 部署/验收 | 本节末尾 |
| §12 兼容性 | Task 8 — 不动 statistics router 现有 5 个 endpoint |
| §13 风险 | Task 5(并发);Task 7(50000 上限);Task 4(单库失败 catch) |

## 备注

- 平台 admin 用户(`platform_user` 行 role='admin', hospital_id=NULL)需在部署时存在;若 dev 环境无,需要手工 `INSERT` 或通过 `/auth/register` 注册一个 role=admin 的账号。这部分不进本计划交付范围。
- `hospital_template` 数据库的连接在 `_get_tenant_names` 里用 `get_session("hospital_template")` —— 它就是 `settings.MYSQL_TEMPLATE_DB`(`config.py` 已有),与其余 template 操作一致。
- ECharts 首次接入 admin-portal可能会有 TS 类型补丁需求(`echarts-for-react` 的 React 18 兼容);如 `pnpm build` 报错可参考 `frontend/packages/doctor-portal` 处理(本计划 task 10 的 Step 5 已含编译验证步骤)。
- `_get_tenant_names` 中 `hospital_template` 模式:若 `settings.MYSQL_TEMPLATE_DB` 不叫 `hospital_template`,改为 `settings.MYSQL_TEMPLATE_DB` 引用。实现时应: `from app.config import settings` 然后 `get_session(settings.MYSQL_TEMPLATE_DB)`。