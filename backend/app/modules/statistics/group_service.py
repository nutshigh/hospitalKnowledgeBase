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
