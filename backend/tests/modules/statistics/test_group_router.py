from datetime import date
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

import app.main as main_mod
from app.core.dependencies import get_current_user, CurrentUser


def test_group_routes_registered():
    paths = {getattr(r, "path", None) for r in main_mod.app.routes}
    assert "/api/v1/statistics/group/overview" in paths
    assert "/api/v1/statistics/group/high-risk" in paths


def test_overview_admin_only_non_admin_forbidden():
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="doctor", hospital_id=None)
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/statistics/group/overview?group_by=hospital")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_overview_admin_returns_200(monkeypatch):
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="admin", hospital_id=None)
    fake = {"group_by": "hospital", "filters": {}, "rows": [], "totals": {}}
    monkeypatch.setattr("app.modules.statistics.group_router.get_overview",
                         lambda gb, f: fake)
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/statistics/group/overview?group_by=hospital")
        assert r.status_code == 200
        assert r.json()["group_by"] == "hospital"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_overview_invalid_group_by_returns_422():
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="admin", hospital_id=None)
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/statistics/group/overview?group_by=foo")
        assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_high_risk_csv_endpoint_returns_blob(monkeypatch):
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=1, role="admin", hospital_id=None)
    def fake_stream(f, s):
        yield "\ufeffhospital_id\nH001\n".encode("utf-8")
    monkeypatch.setattr("app.modules.statistics.group_router.stream_high_risk_csv",
                         fake_stream)
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/statistics/group/high-risk?format=csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert r.content.startswith("\ufeff".encode("utf-8"))
    finally:
        app.dependency_overrides.pop(get_current_user, None)
