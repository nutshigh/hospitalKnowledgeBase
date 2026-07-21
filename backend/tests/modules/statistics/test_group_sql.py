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
