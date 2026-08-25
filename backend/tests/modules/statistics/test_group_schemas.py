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
