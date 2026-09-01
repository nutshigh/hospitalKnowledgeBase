"""app-login 免密登录:key 校验 / 双锚定解析 / 自动注册 / 错误码。"""
import pytest
from datetime import timedelta

from app.api.auth import app_login, AppLoginRequest
from app.core.hospital_resolver import ResolverUnavailableError
from app.utils.exceptions import (
    UnauthorizedException, ValidationException, ServiceUnavailableException,
)


def _req(**kw):
    defaults = {"app_key": "secret", "name": "张三", "id_card_suffix": "12345X"}
    defaults.update(kw)
    return AppLoginRequest(**defaults)


class _Row:
    def __init__(self, user_id=1, role="user", hospital_id="H001",
                 id_card_suffix="12345X", name="张三"):
        self.id = user_id
        self.role = role
        self.hospital_id = hospital_id
        self.id_card_suffix = id_card_suffix
        self.name = name


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeDB:
    def __init__(self, user_exists=False, commit_fails=False):
        self.user_exists = user_exists
        self.commit_fails = commit_fails
        self.inserted = []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))
        sql = str(sql)
        if sql.lstrip().startswith("INSERT"):
            self.inserted.append(params)
            return _FakeResult(None)
        if "SELECT id, role" in sql:
            if self.user_exists:
                return _FakeResult(_Row())
            return _FakeResult(None)
        return _FakeResult(None)

    def commit(self):
        if self.commit_fails:
            from sqlalchemy.exc import IntegrityError
            raise IntegrityError("stmt", {}, Exception("dup"))
        self.user_exists = True

    def rollback(self):
        pass


class _FakeRaceDB:
    """模拟并发竞态:首次 SELECT 无行 → INSERT → commit 撞唯一索引抛 IntegrityError
    → rollback → 回查 SELECT 返回并发事务已插入的行。"""

    def __init__(self):
        self.inserted = []
        self.select_calls = 0

    def execute(self, sql, params=None):
        sql = str(sql)
        if sql.lstrip().startswith("INSERT"):
            self.inserted.append(params)
            return _FakeResult(None)
        if "SELECT id, role" in sql:
            self.select_calls += 1
            if self.select_calls == 1:
                return _FakeResult(None)
            return _FakeResult(_Row())
        return _FakeResult(None)

    def commit(self):
        from sqlalchemy.exc import IntegrityError
        raise IntegrityError("stmt", {}, Exception("dup"))

    def rollback(self):
        pass


@pytest.fixture
def ctx(monkeypatch):
    import app.api.auth as auth
    monkeypatch.setattr(auth.settings, "APP_API_KEY", "secret")
    monkeypatch.setattr(auth.settings, "APP_LOGIN_TOKEN_EXPIRE_MINUTES", 10080)
    monkeypatch.setattr(auth, "resolve_hospital", lambda suf: "H001")
    calls = {}

    def fake_create(data, expires_delta=None):
        calls["data"] = data
        calls["expires_delta"] = expires_delta
        return "tok"

    monkeypatch.setattr(auth, "create_access_token", fake_create)
    return auth, calls


def test_app_login_existing_user(ctx):
    auth, calls = ctx
    resp = app_login(_req(), db=_FakeDB(user_exists=True))
    assert resp.access_token == "tok"
    assert resp.role == "user"
    assert resp.id_card_suffix == "12345X"
    assert resp.name == "张三"
    assert calls["data"]["id_card_suffix"] == "12345X"
    assert calls["data"]["name"] == "张三"
    assert calls["data"]["hospital_id"] == "H001"
    assert calls["expires_delta"] == timedelta(minutes=10080)


def test_app_login_auto_registers_and_idempotent(ctx):
    auth, _ = ctx
    db = _FakeDB(user_exists=False)
    resp = app_login(_req(), db=db)
    assert resp.id_card_suffix == "12345X"
    assert len(db.inserted) == 1
    assert db.inserted[0]["un"] == "app_H001_张三_12345X"
    assert db.inserted[0]["r"] == "user"
    app_login(_req(), db=db)          # 二次调用命中已有行
    assert len(db.inserted) == 1


def test_app_login_race_integrity_error_idempotent(ctx):
    auth, _ = ctx
    db = _FakeRaceDB()
    resp = app_login(_req(), db=db)
    assert resp.id_card_suffix == "12345X"
    assert len(db.inserted) == 1


def test_app_login_wrong_key(ctx):
    auth, _ = ctx
    with pytest.raises(UnauthorizedException):
        app_login(_req(app_key="wrong"), db=_FakeDB())


def test_app_login_unconfigured_key_rejected(ctx):
    auth, _ = ctx
    auth.settings.APP_API_KEY = ""
    with pytest.raises(UnauthorizedException):
        app_login(_req(), db=_FakeDB())


def test_app_login_bad_suffix(ctx):
    auth, _ = ctx
    with pytest.raises(ValidationException):
        app_login(_req(id_card_suffix="12bad"), db=_FakeDB())


def test_app_login_missing_name(ctx):
    auth, _ = ctx
    with pytest.raises(ValidationException):
        app_login(_req(name=""), db=_FakeDB())


def test_app_login_resolver_no_match(ctx):
    auth, _ = ctx
    auth.resolve_hospital = lambda suf: None
    with pytest.raises(UnauthorizedException):
        app_login(_req(), db=_FakeDB())


def test_app_login_resolver_unavailable(ctx):
    auth, _ = ctx
    def boom(suf):
        raise ResolverUnavailableError("down")
    auth.resolve_hospital = boom
    with pytest.raises(ServiceUnavailableException):
        app_login(_req(), db=_FakeDB())
