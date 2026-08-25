from datetime import date
from app.modules.statistics.group_schemas import GroupFilters
from app.modules.statistics.group_sql import (
    build_overview_sql,
    build_top_abnormal_sql, build_sub_gender_sql,
    build_sub_age_group_sql, build_high_risk_list_sql,
)


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
    assert "ri.name" in sql
    assert "ORDER BY interp.red_count DESC" in sql
    assert "LIMIT :limit OFFSET :offset" in sql
    assert "JOIN hospital_user" not in sql
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
    assert "limit" not in p and "offset" not in p


def test_high_risk_list_batch_filter_join():
    f = GroupFilters(batch_ids=["x1"])
    sql, _ = build_high_risk_list_sql(f, sort="red_count", limit=10, offset=0)
    assert "LEFT JOIN batch_import_file bf" in sql
    assert "LEFT JOIN batch_import b" in sql
