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
    where = where + (
        " AND (interp.overall_level IN ('red','yellow') OR interp.red_count >= 3)"
    )
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
        + BATCH_JOIN
    )
    sql += " WHERE " + where + f" ORDER BY {sort_col} DESC LIMIT :limit OFFSET :offset"
    params["limit"] = limit
    params["offset"] = offset
    return sql, params
