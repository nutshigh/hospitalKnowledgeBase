"""hospital_resolver 单测:精确匹配 / 无匹配 / 歧义 / 业务错误 / 宕机 / 未配置。"""
import httpx
import pytest

from app.core import hospital_resolver


@pytest.fixture(autouse=True)
def _reset_client():
    hospital_resolver._shared_client = None
    yield
    hospital_resolver._shared_client = None


def test_resolve_hospital_matches(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "操作成功",
                    "data": [{"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002}]}

    def fake_get(url, params):
        assert url == "http://x/biz/baUserOpen/searchUser"
        assert params == {"realName": "张三", "idCardLast6": "12345X"}
        return FakeResp()

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(fake_get))
    assert hospital_resolver.resolve_hospital("张三", "12345X") == "1000002"


def test_resolve_hospital_exact_filter_ignores_others(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok",
                    "data": [
                        {"realName": "张三丰", "idCardLast6": "12345X", "orgId": 9000},
                        {"realName": "张三", "idCardLast6": "99999X", "orgId": 8000},
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002},
                    ]}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "12345X") == "1000002"


def test_resolve_hospital_no_match_empty_array(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok", "data": []}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "123456") is None


def test_resolve_hospital_null_data(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok", "data": None}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "123456") is None


def test_resolve_hospital_ambiguous_returns_none_and_warns(monkeypatch, caplog):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok",
                    "data": [
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002},
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000003},
                    ]}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    with caplog.at_level("WARNING", logger="app.batch.extract.resolver"):
        assert hospital_resolver.resolve_hospital("张三", "12345X") is None
    assert any("resolver ambiguous" in rec.message for rec in caplog.records)


def test_resolve_hospital_same_orgid_multi_records(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok",
                    "data": [
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002},
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002},
                    ]}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "12345X") == "1000002"


def test_resolve_hospital_business_code_500_raises(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 500, "msg": "内部错误", "data": None}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("张三", "123456")


def test_resolve_hospital_http_404_is_no_match(monkeypatch, caplog):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 404
        def json(self):
            return {}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    with caplog.at_level("WARNING", logger="app.batch.extract.resolver"):
        assert hospital_resolver.resolve_hospital("张三", "123456") is None
    assert any("resolver 4xx status=404" in rec.message for rec in caplog.records)


def test_resolve_hospital_http_500_raises_unavailable(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 500
        def json(self):
            return {}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("张三", "123456")


def test_resolve_hospital_timeout_raises_unavailable(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    def boom(url, params):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(boom))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("张三", "123456")


def test_resolve_hospital_url_not_configured_returns_none(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "")
    assert hospital_resolver.resolve_hospital("张三", "123456") is None


def test_resolve_hospital_bad_json_raises(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            raise ValueError("bad json")

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("张三", "123456")


def test_resolve_hospital_bad_data_shape_raises(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok", "data": {"records": []}}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("张三", "123456")


def test_resolve_hospital_null_orgid_skipped(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 200, "msg": "ok",
                    "data": [
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": None},
                        {"realName": "张三", "idCardLast6": "12345X", "orgId": 1000002},
                    ]}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, params: FakeResp()))
    assert hospital_resolver.resolve_hospital("张三", "12345X") == "1000002"


class _StubClient:
    def __init__(self, get_fn):
        self._get = get_fn
    def get(self, url, params=None):
        return self._get(url, params)
    @property
    def is_closed(self):
        return False
