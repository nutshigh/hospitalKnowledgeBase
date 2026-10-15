"""VLMClient(OCR) 重试与整体故障判定单测(2026-10-14)。

- 单页请求失败重试(1s/3s 退避, 仅瞬态错误);
- `_ocr_outage`: 大面积失败 → OcrUnavailableError(worker 记 failed_stage=ocr_failed
  可重试); 个别页偶发失败仍按历史行为跳过(不阻断整份报告)。
"""
from unittest.mock import patch

import httpx
import pytest

from app.config import settings
from app.core.vlm_client import VLMClient, OcrUnavailableError, _ocr_outage


class _FakeResp:
    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload or {"markdown": ""}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err", request=httpx.Request("POST", "http://x/ocr"),
                response=httpx.Response(self.status_code))

    def json(self):
        return self._payload


def _client():
    return VLMClient()


# ---------- _ocr_outage ----------

@pytest.mark.parametrize("attempted,failed,expect", [
    (0, 0, False), (1, 1, False), (2, 1, False), (2, 2, True),
    (10, 4, False), (10, 5, True), (4, 2, True), (3, 2, True),
    (6, 2, False), (20, 20, True),
])
def test_ocr_outage_combos(attempted, failed, expect):
    assert _ocr_outage(attempted, failed) is expect


# ---------- 单页重试 ----------

def test_retry_then_success():
    c = _client()
    calls = {"n": 0}

    def _post(url, json=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return _FakeResp({"markdown": "| 项目 | 结果 |\n| --- | --- |\n"})

    with patch.object(c.client, "post", side_effect=_post), \
            patch("app.core.vlm_client.time.sleep"):
        out = c.extract_from_image("AAAA")
    assert calls["n"] == 2
    assert "raw_text" in out


def test_retry_exhausted_raises():
    c = _client()

    def _post(url, json=None):
        raise httpx.ConnectError("refused")

    with patch.object(c.client, "post", side_effect=_post), \
            patch("app.core.vlm_client.time.sleep"):
        with pytest.raises(httpx.ConnectError):
            c.extract_from_image("AAAA")


def test_4xx_no_retry():
    c = _client()
    calls = {"n": 0}

    def _post(url, json=None):
        calls["n"] += 1
        return _FakeResp(status_code=400)

    with patch.object(c.client, "post", side_effect=_post), \
            patch("app.core.vlm_client.time.sleep"):
        with pytest.raises(httpx.HTTPStatusError):
            c.extract_from_image("AAAA")
    assert calls["n"] == 1


# ---------- extract_from_images 故障阈值 ----------

def test_all_pages_fail_raises_outage():
    c = _client()
    with patch.object(c, "extract_from_image", side_effect=httpx.ConnectError("x")):
        with pytest.raises(OcrUnavailableError):
            c.extract_from_images(["a", "b", "c"])


def test_single_page_failure_tolerated():
    c = _client()
    calls = {"n": 0}

    def _extract(img):
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.ConnectError("x")
        return {"personal_info": {}, "indicators": [{"item_name": f"项{calls['n']}"}],
                "raw_text": "t"}

    with patch.object(c, "extract_from_image", side_effect=_extract):
        out = c.extract_from_images(["a", "b", "c", "d", "e"])
    assert len(out["indicators"]) == 4  # 1 页跳过, 不抛
