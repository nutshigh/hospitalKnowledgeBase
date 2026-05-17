from typing import Optional, Dict
from sqlalchemy.orm import Session
from sqlalchemy import text


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
    total = row.total_reports or 0
    red = row.red_reports or 0
    yellow = row.yellow_reports or 0
    return {
        "total_reports": total,
        "red_reports": red,
        "yellow_reports": yellow,
        "abnormal_rate": round((red + yellow) / total * 100, 1) if total else 0,
        "avg_red_indicators": float(row.avg_red_indicators or 0),
    }


def health_profile(db: Session, start_date: str, end_date: str,
                   unit_name: Optional[str] = None) -> dict:
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
    if x_dimension == "age_group":
        dim_col = """CASE
            WHEN ri.age < 30 THEN '<30'
            WHEN ri.age BETWEEN 30 AND 45 THEN '30-45'
            WHEN ri.age BETWEEN 46 AND 60 THEN '46-60'
            ELSE '>60'
        END"""
    elif x_dimension == "gender":
        dim_col = "ri.gender"
    else:
        dim_col = "ri.unit_name"

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
            {"label": r.dimension or "未知", "total": r.total,
             "red": r.red_cnt, "yellow": r.yellow_cnt}
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
            {"year": r.year, "total": r.total, "red": r.red_cnt,
             "yellow": r.yellow_cnt,
             "abnormal_rate": round((r.red_cnt + r.yellow_cnt) / r.total * 100, 1) if r.total else 0}
            for r in rows
        ]
    }


def cross_hospital_summary(dbs: Dict[str, Session]) -> dict:
    results = []
    for hospital_id, db in dbs.items():
        try:
            row = db.execute(text("SELECT COUNT(*) as cnt FROM report_info")).fetchone()
            results.append({"hospital_id": hospital_id, "total_reports": row.cnt if row else 0})
        except Exception:
            results.append({"hospital_id": hospital_id, "total_reports": 0, "error": "unavailable"})
    return {"hospitals": results}
