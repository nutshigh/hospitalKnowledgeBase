from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user, CurrentUser
from app.modules.report.router import router as report_router


def _client_with_indicators(indicator_names):
    app = FastAPI()
    app.include_router(report_router, prefix="/api/v1/reports")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="user", hospital_id="1", id_card_suffix="100001", name="测试1"
    )
    report = SimpleNamespace(
        id=1, task_id=1, name="测试1", parsed_name=None,
        gender=None, age=None, report_date=None, check_type=None,
        unit_name=None, created_at=datetime(2026, 1, 1),
    )
    inds = [SimpleNamespace(
        item_name=n, item_name_standard=None, item_code=None,
        result_value="1", unit=None, ref_range_low=None, ref_range_high=None,
        category=None,
    ) for n in indicator_names]
    patches = [
        patch("app.modules.report.router.service.get_report_detail", return_value=report),
        patch("app.modules.report.router.service.get_report_indicators", return_value=inds),
        patch("app.modules.report.router.service.get_task_status",
              return_value=SimpleNamespace(status="completed")),
    ]
    for p in patches:
        p.start()
    client = TestClient(app)
    try:
        r = client.get("/api/v1/reports/1")
    finally:
        for p in patches:
            p.stop()
    return r


def test_report_detail_indicators_carry_group():
    r = _client_with_indicators(["白细胞", "血红蛋白", "尿潜血", "肿瘤特异生长因子"])
    assert r.status_code == 200, r.text
    data = r.json()
    by_name = {i["item_name"]: i.get("group") for i in data["indicators"]}
    assert by_name["白细胞"] == "血常规"
    assert by_name["血红蛋白"] == "血常规"
    assert by_name["尿潜血"] == "尿常规"
    assert by_name["肿瘤特异生长因子"] is None
    assert data["module_order"] == ["血常规", "尿常规"]


def test_report_detail_module_order_excel_sequence():
    r = _client_with_indicators(["谷丙转氨酶", "血红蛋白", "舒张压"])
    assert r.status_code == 200, r.text
    # Excel 顺序:身高体重血压(9) < 血常规(103) < 肝功能(193)
    assert r.json()["module_order"] == ["身高体重血压", "血常规", "肝功能"]
