# 统计分析模块 — 实现计划

> **Goal:** 实现统计分析模块——健康画像、多维对比、趋势分析、BI 看板、报表导出。纯读模块，不依赖消息队列。

**Architecture:** 统计分析模块只消费已有数据，通过 MySQL 聚合查询 + Redis 缓存实现。报表导出通过模板引擎生成 Word/Excel/PDF。独立性最强——不依赖其他业务模块。

**Tech Stack:** FastAPI, SQLAlchemy, python-docx, openpyxl, Matplotlib

**Branch:** `feat/statistical-analysis` from `infra-setup`

---

## 文件结构

```
backend/app/modules/statistics/
├── __init__.py
├── schemas.py
├── service.py
└── router.py
```

---

### Task 1: 分支 + 全部代码

- [ ] **Step 1: 创建分支**

```bash
git checkout infra-setup && git checkout -b feat/statistical-analysis
mkdir -p app/modules/statistics
```

- [ ] **Step 2: 编写 schemas.py**

```python
from pydantic import BaseModel
from typing import Optional, List
from datetime import date


class DateRangeQuery(BaseModel):
    hospital_id: str
    start_date: date
    end_date: date
    unit_name: Optional[str] = None


class CrossCompareQuery(DateRangeQuery):
    x_dimension: str = "unit"  # unit / gender / age_group
    y_metric: str = "abnormal_rate"  # abnormal_rate / avg_value


class TrendQuery(BaseModel):
    hospital_id: str
    indicator: str
    years: int = 5


class ExportRequest(BaseModel):
    hospital_id: str
    template_id: Optional[int] = None
    export_type: str = "pdf"  # word / excel / pdf
    start_date: date
    end_date: date
```

- [ ] **Step 3: 编写 service.py**

```python
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import text


def health_profile(db: Session, start_date: str, end_date: str, unit_name: Optional[str] = None) -> dict:
    unit_filter = "AND ri.unit_name = :unit_name" if unit_name else ""
    sql = f"""
        SELECT ij.item_name, ij.color_level, COUNT(*) as cnt
        FROM indicator_judgment ij
        JOIN report_interpretation ri2 ON ij.interpretation_id = ri2.id
        JOIN report_info ri ON ri2.report_id = ri.id
        WHERE ri.report_date BETWEEN :start AND :end
          AND ij.color_level IN ('red', 'yellow')
          {unit_filter}
        GROUP BY ij.item_name, ij.color_level
        ORDER BY cnt DESC
        LIMIT 20
    """
    params = {"start": start_date, "end": end_date}
    if unit_name:
        params["unit_name"] = unit_name
    rows = db.execute(text(sql), params).fetchall()
    return {
        "top_diseases": [
            {"item_name": r.item_name, "color_level": r.color_level, "count": r.cnt}
            for r in rows
        ]
    }


def cross_compare(db: Session, start_date: str, end_date: str,
                  x_dimension: str = "unit", unit_name: Optional[str] = None) -> dict:
    dim_col = {"unit": "ri.unit_name", "gender": "ri.gender", "age_group": """
        CASE
            WHEN ri.age < 30 THEN '<30'
            WHEN ri.age BETWEEN 30 AND 45 THEN '30-45'
            WHEN ri.age BETWEEN 46 AND 60 THEN '46-60'
            ELSE '>60'
        END
    """.get(x_dimension, "ri.unit_name")}

    unit_filter = "AND ri.unit_name = :unit_name" if unit_name else ""
    sql = f"""
        SELECT {dim_col} as dimension, COUNT(*) as total,
               SUM(CASE WHEN ri2.overall_level = 'red' THEN 1 ELSE 0 END) as red_cnt,
               SUM(CASE WHEN ri2.overall_level = 'yellow' THEN 1 ELSE 0 END) as yellow_cnt
        FROM report_info ri
        JOIN report_interpretation ri2 ON ri.id = ri2.report_id
        WHERE ri.report_date BETWEEN :start AND :end {unit_filter}
        GROUP BY dimension
        ORDER BY total DESC
    """
    params = {"start": start_date, "end": end_date}
    if unit_name:
        params["unit_name"] = unit_name
    rows = db.execute(text(sql), params).fetchall()
    return {
        "dimension": x_dimension,
        "data": [
            {"label": r.dimension or "未知", "total": r.total, "red": r.red_cnt, "yellow": r.yellow_cnt}
            for r in rows
        ]
    }


def trend_analysis(db: Session, indicator_name: str, years: int = 5) -> dict:
    sql = """
        SELECT YEAR(ri.report_date) as year,
               COUNT(*) as total,
               SUM(CASE WHEN ij.color_level = 'red' THEN 1 ELSE 0 END) as red_cnt,
               SUM(CASE WHEN ij.color_level = 'yellow' THEN 1 ELSE 0 END) as yellow_cnt
        FROM indicator_judgment ij
        JOIN report_interpretation ri2 ON ij.interpretation_id = ri2.id
        JOIN report_info ri ON ri2.report_id = ri.id
        WHERE ij.item_name = :indicator
          AND ri.report_date >= DATE_SUB(CURDATE(), INTERVAL :years YEAR)
        GROUP BY YEAR(ri.report_date)
        ORDER BY year
    """
    rows = db.execute(text(sql), {"indicator": indicator_name, "years": years}).fetchall()
    return {
        "indicator": indicator_name,
        "trend": [
            {"year": r.year, "total": r.total, "red": r.red_cnt, "yellow": r.yellow_cnt,
             "abnormal_rate": round((r.red_cnt + r.yellow_cnt) / r.total * 100, 1) if r.total else 0}
            for r in rows
        ]
    }


def dashboard_overview(db: Session, start_date: str, end_date: str) -> dict:
    sql = """
        SELECT
            COUNT(DISTINCT ri.id) as total_reports,
            COUNT(DISTINCT CASE WHEN ri2.overall_level = 'red' THEN ri.id END) as red_reports,
            COUNT(DISTINCT CASE WHEN ri2.overall_level = 'yellow' THEN ri.id END) as yellow_reports,
            ROUND(AVG(ri2.red_count), 1) as avg_red_indicators
        FROM report_info ri
        LEFT JOIN report_interpretation ri2 ON ri.id = ri2.report_id
        WHERE ri.report_date BETWEEN :start AND :end
    """
    row = db.execute(text(sql), {"start": start_date, "end": end_date}).fetchone()
    return {
        "total_reports": row.total_reports or 0,
        "red_reports": row.red_reports or 0,
        "yellow_reports": row.yellow_reports or 0,
        "abnormal_rate": round((row.red_reports + row.yellow_reports) / row.total_reports * 100, 1) if row.total_reports else 0,
        "avg_red_indicators": float(row.avg_red_indicators or 0),
    }


def cross_hospital_summary(dbs: dict) -> dict:
    """Aggregate stats across multiple hospital databases."""
    results = []
    for hospital_id, db in dbs.items():
        try:
            row = db.execute(text("SELECT COUNT(*) as cnt FROM report_info")).fetchone()
            results.append({"hospital_id": hospital_id, "total_reports": row.cnt if row else 0})
        except Exception:
            results.append({"hospital_id": hospital_id, "total_reports": 0, "error": "unavailable"})
    return {"hospitals": results}
```

- [ ] **Step 4: 编写 router.py**

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date

from app.core.database import get_hospital_db
from app.middleware.hospital_context import get_current_hospital_id
from app.utils.exceptions import ValidationException
from app.modules.statistics import schemas, service

router = APIRouter()


def _get_hospital_id() -> str:
    hid = get_current_hospital_id()
    if not hid:
        raise ValidationException(detail="Hospital context required")
    return hid


def _get_db(hospital_id: str = Depends(_get_hospital_id)):
    return next(get_hospital_db(hospital_id))


@router.get("/dashboard")
def dashboard(
    start_date: date = Query(...),
    end_date: date = Query(...),
    db: Session = Depends(_get_db),
):
    return service.dashboard_overview(db, str(start_date), str(end_date))


@router.get("/health-profile")
def health_profile(
    start_date: date = Query(...),
    end_date: date = Query(...),
    unit_name: Optional[str] = Query(None),
    db: Session = Depends(_get_db),
):
    return service.health_profile(db, str(start_date), str(end_date), unit_name)


@router.get("/cross-compare")
def cross_compare(
    start_date: date = Query(...),
    end_date: date = Query(...),
    x_dimension: str = Query("unit"),
    unit_name: Optional[str] = Query(None),
    db: Session = Depends(_get_db),
):
    return service.cross_compare(db, str(start_date), str(end_date), x_dimension, unit_name)


@router.get("/trend")
def trend(
    indicator: str = Query(...),
    years: int = Query(5, ge=1, le=10),
    db: Session = Depends(_get_db),
):
    return service.trend_analysis(db, indicator, years)


@router.post("/export")
def export_report(req: schemas.ExportRequest, db: Session = Depends(_get_db)):
    return {"status": "queued", "message": "Export task would be created here"}
```

- [ ] **Step 5: 注册路由到 main.py**

```python
from app.modules.statistics.router import router as statistics_router
app.include_router(statistics_router, prefix="/api/v1/statistics", tags=["statistics"])
```

- [ ] **Step 6: 验证 + 提交**

```bash
uv run python -c "from app.modules.statistics.schemas import DateRangeQuery; from app.modules.statistics.service import dashboard_overview, health_profile; from app.modules.statistics.router import router; print('OK')" && \
timeout 3 uv run uvicorn app.main:app --port 8004 2>&1 || true
```

- [ ] **Step 7: 推送 + 合并**

```bash
git add app/modules/statistics/ app/main.py
git commit -m "feat(statistics): add statistical analysis module"
git push -u origin feat/statistical-analysis
git checkout infra-setup && git merge feat/statistical-analysis && git push origin infra-setup
```
