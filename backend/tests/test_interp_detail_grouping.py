from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_user, CurrentUser
from app.modules.interpretation.router import router as interp_router


def _client_with_judgments(item_names):
    app = FastAPI()
    app.include_router(interp_router, prefix="/api/v1/interpretations")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="user", hospital_id="1", id_card_suffix="100001", name="测试1"
    )
    interp = SimpleNamespace(
        id=1, report_id=1, overall_level="yellow",
        red_count=0, yellow_count=1, green_count=1, status="completed",
        summary_text="{}", summary_refs=[], quality_note=None,
        created_at=datetime(2026, 1, 1), completed_at=datetime(2026, 1, 1),
    )
    rows = [
        {"indicator_id": i + 1, "item_name": n, "result_value": "1",
         "deviation": "normal", "color_level": "green", "unit": None,
         "ref_range_low": None, "ref_range_high": None}
        for i, n in enumerate(item_names)
    ]
    patches = [
        patch("app.modules.interpretation.router.service.get_interpretation", return_value=interp),
        patch("app.modules.interpretation.router.service.get_judgments_with_indicator_detail",
              return_value=rows),
    ]
    for p in patches:
        p.start()
    client = TestClient(app)
    try:
        r = client.get("/api/v1/interpretations/1")
    finally:
        for p in patches:
            p.stop()
    return r


def test_interpretation_indicators_carry_group():
    r = _client_with_judgments(["白细胞", "血红蛋白", "游离甲状腺素(FT4)测定", "肿瘤特异生长因子"])
    assert r.status_code == 200, r.text
    data = r.json()
    by_name = {i["item_name"]: i.get("group") for i in data["indicators"]}
    assert by_name["白细胞"] == "血常规"
    assert by_name["血红蛋白"] == "血常规"
    assert by_name["游离甲状腺素(FT4)测定"] == "甲状腺功能"
    assert by_name["肿瘤特异生长因子"] is None
    assert data["module_order"] == ["血常规", "甲状腺功能"]


def test_interpretation_module_order_excel_sequence():
    r = _client_with_judgments(["甲胎蛋白(AFP)定量", "白细胞", "舒张压"])
    assert r.status_code == 200, r.text
    # Excel 顺序:身高体重血压(9) < 血常规(103) < 甲胎蛋白(AFP)定量(325)
    assert r.json()["module_order"] == ["身高体重血压", "血常规", "甲胎蛋白(AFP)定量"]
