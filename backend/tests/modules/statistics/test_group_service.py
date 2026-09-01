from datetime import date
from unittest.mock import MagicMock, patch

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


from app.modules.statistics.group_service import get_overview


def test_get_overview_merges_multi_tenant(monkeypatch):
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
    assert r["totals"]["total_people"] == 100


def test_get_overview_empty_tenants(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service.get_all_hospital_ids",
                         lambda: [])
    r = get_overview("hospital", GroupFilters())
    assert r["rows"] == []
    assert r["totals"]["total_people"] == 0


def test_per_tenant_overview_get_session_failure_is_isolated():
    """C1 regression: get_session raising must NOT escape _per_tenant_overview;
    must return error row, not propagate to get_overview."""
    with patch("app.modules.statistics.group_service.get_session",
               side_effect=RuntimeError("engine build failed")):
        res = _per_tenant_overview("H001", "H", "hospital", GroupFilters())
    assert res == {"key": "H001", "label": "H", "error": "db_unavailable"}


def test_get_overview_handles_get_session_failure_per_tenant(monkeypatch):
    """C1 e2e: if get_session fails for one tenant only, others should still work."""
    monkeypatch.setattr("app.modules.statistics.group_service.get_all_hospital_ids",
                         lambda: ["H001", "H002"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "A", "H002": "B"})

    def fake_per(hid, hname, gb, f):
        if hid == "H001":
            # simulate that get_session raises; _per_tenant_overview should catch
            with patch("app.modules.statistics.group_service.get_session",
                       side_effect=RuntimeError("boom")):
                return _per_tenant_overview(hid, hname, gb, f)
        return {"key": hid, "label": hname,
                "total_people": 100, "red_count": 1, "yellow_count": 0,
                "green_count": 99, "abnormal_rate": 0.01}

    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_overview",
                         fake_per)
    r = get_overview("hospital", GroupFilters())
    assert any(row["key"] == "H001" and row.get("error") == "db_unavailable"
               for row in r["rows"])
    assert r["totals"]["total_people"] == 100  # only H002


def test_get_high_risk_sort_report_date_handles_mixed_nulls(monkeypatch):
    """I1 regression: sort=report_date with mixed None/non-None should not TypeError."""
    monkeypatch.setattr("app.modules.statistics.group_service._resolve_tenants",
                         lambda f: ["H001"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "A"})
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_count",
                         lambda hid, f: 3)
    rows = [
        {"hospital_id": "H001", "hospital_name": "A", "report_id": 1, "user_id": "1001",
         "name": "u1", "gender": "M", "age": 40, "report_date": None,
         "batch_id": None, "batch_name": None, "overall_level": "red",
         "red_count": 1, "yellow_count": 0, "summary_text": ""},
        {"hospital_id": "H001", "hospital_name": "A", "report_id": 2, "user_id": "1002",
         "name": "u2", "gender": "F", "age": 35, "report_date": date(2026, 1, 1),
         "batch_id": None, "batch_name": None, "overall_level": "red",
         "red_count": 1, "yellow_count": 0, "summary_text": ""},
        {"hospital_id": "H001", "hospital_name": "A", "report_id": 3, "user_id": "1003",
         "name": "u3", "gender": "M", "age": 50, "report_date": date(2026, 2, 1),
         "batch_id": None, "batch_name": None, "overall_level": "yellow",
         "red_count": 3, "yellow_count": 1, "summary_text": ""},
    ]
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_rows",
                         lambda *a, **k: rows)
    from app.modules.statistics.group_service import get_high_risk
    r = get_high_risk(GroupFilters(), sort="report_date", page=1, page_size=10)
    assert len(r["items"]) == 3  # no TypeError raised
    # Null reports should sort last when reverse=True
    assert r["items"][-1]["report_date"] is None


def test_get_high_risk_basic_total_and_pagination(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service._resolve_tenants",
                         lambda f: ["H001"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "医院A"})
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_count",
                         lambda hid, f: 233)
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_rows",
                         lambda hid, name, f, s, lim, off:
                         [{"hospital_id": hid, "hospital_name": name,
                           "report_id": i, "user_id": str(i * 10), "name": f"u{i}",
                           "gender": "M", "age": 40,
                           "report_date": date(2026, 1, 1),
                           "batch_id": None, "batch_name": None,
                           "overall_level": "red", "red_count": 1, "yellow_count": 0,
                           "summary_text": "s"} for i in range(20)])
    from app.modules.statistics.group_service import get_high_risk
    r = get_high_risk(GroupFilters(), sort="red_count", page=1, page_size=20)
    assert r["total"] == 233
    assert r["page"] == 1 and r["page_size"] == 20
    assert len(r["items"]) == 20
    assert r["items"][0]["hospital_id"] == "H001"
    assert r["items"][0]["hospital_name"] == "医院A"
    assert r["items"][0]["red_count"] == 1


def test_get_high_risk_filter_unknown_hospital_id_dropped(monkeypatch):
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
    from app.modules.statistics.group_service import get_high_risk
    r = get_high_risk(GroupFilters(hospital_ids=["H001", "H999"]),
                       sort="red_count", page=1, page_size=20)
    assert r["total"] == 0 and r["items"] == []
    assert counts == {}


import csv
import io
from fastapi import HTTPException
from app.modules.statistics.group_service import stream_high_risk_csv


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
                           "report_id": 1, "user_id": "10", "name": "张三",
                           "gender": "M", "age": 40,
                           "report_date": __import__("datetime").date(2026,1,1),
                           "batch_id": None, "batch_name": None,
                           "overall_level": "red", "red_count": 5,
                           "yellow_count": 0, "summary_text": "s"},
                          {"hospital_id": hid, "hospital_name": name,
                           "report_id": 2, "user_id": "20", "name": "李四",
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
                         lambda hid, f: 30_000)
    try:
        list(stream_high_risk_csv(GroupFilters(), sort="red_count"))
        assert False, "expected HTTPException 413"
    except HTTPException as e:
        assert e.status_code == 413


def test_get_high_risk_pagination_slices_correctly(monkeypatch):
    monkeypatch.setattr("app.modules.statistics.group_service._resolve_tenants",
                         lambda f: ["H001"])
    monkeypatch.setattr("app.modules.statistics.group_service._get_tenant_names",
                         lambda hids: {"H001": "A"})
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_count",
                         lambda hid, f: 50)
    monkeypatch.setattr("app.modules.statistics.group_service._per_tenant_high_risk_rows",
                         lambda hid, name, f, s, lim, off:
                         [{"hospital_id": hid, "hospital_name": name,
                           "report_id": i, "user_id": str(i), "name": f"u{i}",
                           "gender": "M", "age": 40, "report_date": None,
                           "batch_id": None, "batch_name": None,
                           "overall_level": "red", "red_count": 5, "yellow_count": 0,
                           "summary_text": ""} for i in range(50)])
    from app.modules.statistics.group_service import get_high_risk
    p1 = get_high_risk(GroupFilters(), sort="red_count", page=1, page_size=20)
    p3 = get_high_risk(GroupFilters(), sort="red_count", page=3, page_size=20)
    assert len(p1["items"]) == 20
    assert len(p3["items"]) == 10
