from datetime import date
from unittest.mock import MagicMock, patch
from sqlalchemy import text

from app.modules.statistics.group_schemas import GroupFilters
from app.modules.statistics.group_service import _per_tenant_overview, _row_key_label


def _row(**kw):
    r = MagicMock()
    for k, v in kw.items():
        setattr(r, k, v)
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
    assert isinstance(res, list) and len(res) == 2
    assert res[0]["key"] == "30-39" and res[0]["total_people"] == 40


def test_row_key_label_hospital():
    assert _row_key_label("hospital", None, "H001", "杭州第一医院") == ("H001", "杭州第一医院")


def test_row_key_label_age_group():
    assert _row_key_label("age_group", _row(age_group="40-49"), "H", "H") == ("40-49", "40-49")


def test_row_key_label_batch():
    assert _row_key_label("batch", _row(batch_id="u1", batch_name="f.zip"), "H", "H") == ("u1", "f.zip")
