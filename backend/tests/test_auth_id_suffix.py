"""id_card_suffix + name 认证链路:登录带出 / 注册必填 / 唯一性 / 后端校验。"""
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth import register, RegisterRequest
from app.core.dependencies import CurrentUser, get_current_user
from app.utils.exceptions import ValidationException


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/whoami")
    def whoami(current_user: CurrentUser = Depends(get_current_user)):
        return {
            "user_id": current_user.user_id,
            "id_card_suffix": current_user.id_card_suffix,
            "name": current_user.name,
        }

    return TestClient(app)


def test_current_user_carries_id_card_suffix(client):
    token = _make_token(user_id=5, role="user", hospital_id="H001",
                        id_card_suffix="12345X", name=None)
    with patch("app.core.dependencies.decode_access_token", return_value=token):
        resp = client.get("/whoami", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert resp.json()["id_card_suffix"] == "12345X"


def test_current_user_without_suffix_is_none(client):
    token = _make_token(user_id=5, role="doctor", hospital_id="H001",
                        id_card_suffix=None, name=None)
    with patch("app.core.dependencies.decode_access_token", return_value=token):
        resp = client.get("/whoami", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert resp.json()["id_card_suffix"] is None


def test_current_user_carries_name(client):
    token = _make_token(user_id=5, role="user", hospital_id="H001",
                        id_card_suffix="12345X", name="张三")
    with patch("app.core.dependencies.decode_access_token", return_value=token):
        resp = client.get("/whoami", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "张三"


def test_current_user_without_name_is_none(client):
    token = _make_token(user_id=5, role="doctor", hospital_id="H001",
                        id_card_suffix=None, name=None)
    with patch("app.core.dependencies.decode_access_token", return_value=token):
        resp = client.get("/whoami", headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200
    assert resp.json()["name"] is None


def test_register_requires_name_for_user_role():
    req = RegisterRequest(username="u", password="p", role="user",
                          hospital_id="H001", id_card_suffix="12345X", name=None)
    with pytest.raises(ValidationException):
        register(req, db=_FakeDB())


def test_register_rejects_duplicate_name_suffix():
    req = RegisterRequest(username="u", password="p", role="user",
                          hospital_id="H001", id_card_suffix="12345X", name="张三")
    db = _FakeDB(username_exists=False, duplicate_exists=True)
    with pytest.raises(ValidationException):
        register(req, db=db)


def test_register_requires_valid_suffix_still_works():
    req = RegisterRequest(username="u", password="p", role="user",
                          hospital_id="H001", id_card_suffix="12bad", name="张三")
    with pytest.raises(ValidationException):
        register(req, db=_FakeDB())


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeDB:
    def __init__(self, username_exists=False, duplicate_exists=False):
        self.username_exists = username_exists
        self.duplicate_exists = duplicate_exists
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))
        sql = str(sql)
        if "name = :name" in sql:
            return _FakeResult(("9",) if self.duplicate_exists else None)
        if "SELECT id, role" in sql:
            return _FakeResult((1, "user", "H001", "12345X", "张三"))
        if "platform_user WHERE username = :un" in sql:
            return _FakeResult(("1",) if self.username_exists else None)
        return _FakeResult(None)

    def commit(self):
        pass


def _make_token(user_id, role, hospital_id, id_card_suffix, name):
    return {
        "user_id": user_id, "role": role, "hospital_id": hospital_id,
        "id_card_suffix": id_card_suffix, "name": name,
    }
