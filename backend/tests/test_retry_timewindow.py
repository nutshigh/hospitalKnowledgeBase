from datetime import datetime

import pytest

import app.core.retry as retry_mod
from app.core.retry import backoff_for_retry, is_bulk_window_now


@pytest.mark.parametrize("rc,expected", [(0, 10000), (1, 60000), (2, 600000), (9, 600000)])
def test_backoff(rc, expected):
    assert backoff_for_retry(rc) == expected


@pytest.mark.parametrize("hour,start,end,expected", [
    (23, 22, 8, True),   # 跨午夜窗口内
    (2, 22, 8, True),
    (8, 22, 8, False),   # 边界(开区间)
    (12, 22, 8, False),  # 白天窗口外
    (14, 14, 18, True),  # 同日窗口内
    (18, 14, 18, False),  # 同日边界
])
def test_bulk_window(hour, start, end, expected, monkeypatch):
    monkeypatch.setenv("BULK_WINDOW_START", str(start))
    monkeypatch.setenv("BULK_WINDOW_END", str(end))
    fake_now = datetime(2026, 7, 15, hour, 30, 0)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, *args, **kwargs):
            return fake_now

    monkeypatch.setattr(retry_mod, "datetime", FakeDateTime)
    assert is_bulk_window_now() is expected