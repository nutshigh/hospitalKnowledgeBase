"""路由注册 + 角色/无后缀语义(service 已单测,这里只测路由层)。"""
from fastapi.testclient import TestClient
from unittest.mock import patch
import app.main as main_mod
from app.core.dependencies import get_current_user, CurrentUser


def _override(app, user):
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def test_routes_registered():
    paths = {getattr(r, "path", None) for r in main_mod.app.routes}
    assert "/api/v1/followup/center" in paths
    assert "/api/v1/followup/template" in paths
    assert "/api/v1/followup/{followup_id}/submit" in paths
    assert "/api/v1/notifications" in paths
    assert "/api/v1/notifications/unread-count" in paths


def test_user_no_suffix_center_returns_empty():
    app = _override(main_mod.app, CurrentUser(user_id=5, role="user",
                                              hospital_id="H001",
                                              id_card_suffix=None, name=None))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/followup/center")
        assert r.status_code == 200
        assert r.json() == {"items": [], "total": 0, "page": 1,
                            "page_size": 20, "has_pending": False}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_user_no_suffix_unread_zero():
    app = _override(main_mod.app, CurrentUser(user_id=5, role="user",
                                              hospital_id="H001",
                                              id_card_suffix=None, name=None))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/notifications/unread-count")
        assert r.status_code == 200
        assert r.json() == {"unread_count": 0}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_admin_template_read_non_admin_403():
    app = _override(main_mod.app, CurrentUser(user_id=1, role="doctor", hospital_id="H001"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/followup/template")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_admin_template_write_empty_400():
    app = _override(main_mod.app, CurrentUser(user_id=1, role="admin", hospital_id=None))
    try:
        with TestClient(app) as c:
            # 空题目:真实 service.save_active_template 在校验阶段抛 ValidationException → 400
            # (未触碰模板库连接;get_template_db 依赖惰性建会话)
            r = c.put("/api/v1/followup/template",
                      json={"name": "通用", "questions": []})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_by_report_user_forbidden():
    app = _override(main_mod.app, CurrentUser(user_id=5, role="user",
                                              hospital_id="H001",
                                              id_card_suffix="123456", name="张三"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/followup/by-report/1")
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_doctor_by_report_404_when_none(monkeypatch):
    app = _override(main_mod.app, CurrentUser(user_id=2, role="doctor", hospital_id="H001"))
    try:
        with TestClient(app) as c:
            with patch("app.modules.followup.router.service.get_followup_by_report",
                       return_value=None):
                r = c.get("/api/v1/followup/by-report/999")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
