"""hospital_resolver 单测:匹配 / 无匹配 / 宕机 / 未配置。"""
import httpx
import pytest

from app.core import hospital_resolver


@pytest.fixture(autouse=True)
def _reset_client():
    hospital_resolver._shared_client = None
    yield
    hospital_resolver._shared_client = None


def test_resolve_hospital_matches(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"hospital_id": "H001"}

    def fake_post(url, json):
        assert url == "http://x/r"
        assert json == {"id_suffix": "12345X"}
        return FakeResp()

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(fake_post))
    assert hospital_resolver.resolve_hospital("12345X") == "H001"


def test_resolve_hospital_no_match_returns_none(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"hospital_id": None}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, json: FakeResp()))
    assert hospital_resolver.resolve_hospital("123456") is None


def test_resolve_hospital_404_is_no_match(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    class FakeResp:
        status_code = 404
        def json(self):
            return {}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, json: FakeResp()))
    assert hospital_resolver.resolve_hospital("123456") is None


def test_resolve_hospital_4xx_logs_warning(monkeypatch, caplog):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    class FakeResp:
        status_code = 400
        text = '{"error": "id not found"}'
        def json(self):
            return {}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, json: FakeResp()))
    with caplog.at_level("WARNING", logger="app.batch.extract.resolver"):
        assert hospital_resolver.resolve_hospital("123456") is None
    assert any("resolver 4xx status=400" in rec.message for rec in caplog.records)


def test_resolve_hospital_500_raises_unavailable(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    class FakeResp:
        status_code = 500
        def json(self):
            return {}

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(lambda url, json: FakeResp()))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("123456")


def test_resolve_hospital_timeout_raises_unavailable(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "http://x/r")

    def boom(url, json):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(hospital_resolver.httpx, "Client",
                        lambda **k: _StubClient(boom))
    with pytest.raises(hospital_resolver.ResolverUnavailableError):
        hospital_resolver.resolve_hospital("123456")


def test_resolve_hospital_url_not_configured_returns_none(monkeypatch):
    monkeypatch.setattr(hospital_resolver.settings, "EXTERNAL_RESOLVER_URL", "")
    assert hospital_resolver.resolve_hospital("123456") is None


class _StubClient:
    def __init__(self, post_fn):
        self._post = post_fn
    def post(self, url, json):
        return self._post(url, json)
    @property
    def is_closed(self):
        return False
