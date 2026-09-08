"""医院切换器数据源:GET /tenants 允许 doctor/admin;user 仍 403。"""
from fastapi.testclient import TestClient

import app.main as main_mod
from app.core.dependencies import get_current_user, CurrentUser
from app.core.database import get_template_db


class _Row:
    def __init__(self, hospital_id, hospital_name, is_active):
        self.hospital_id = hospital_id
        self.hospital_name = hospital_name
        self.is_active = is_active


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return None


class _FakeDB:
    def execute(self, sql, params=None):
        return _Result([
            _Row("H001", "演示医院", 1),
            _Row("1", "市人民医院", 1),
        ])


def _tpl():
    yield _FakeDB()


def _use(user):
    app = main_mod.app
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_template_db] = _tpl
    return app


def _teardown(app):
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_template_db, None)


def test_doctor_can_list_tenants():
    app = _use(CurrentUser(user_id=1, role="doctor", hospital_id="H001"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/tenants")
        assert r.status_code == 200
        ids = {x["hospital_id"] for x in r.json()["items"]}
        assert "H001" in ids and "1" in ids
    finally:
        _teardown(app)


def test_admin_can_list_tenants():
    app = _use(CurrentUser(user_id=3, role="admin", hospital_id="H001"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/tenants")
        assert r.status_code == 200
    finally:
        _teardown(app)


def test_user_cannot_list_tenants():
    app = _use(CurrentUser(user_id=5, role="user", hospital_id="H001",
                           id_card_suffix="123456", name="张三"))
    try:
        with TestClient(app) as c:
            r = c.get("/api/v1/tenants")
        assert r.status_code == 403
    finally:
        _teardown(app)
