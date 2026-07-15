import pytest
from freezegun import freeze_time
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
    with freeze_time(f"2026-07-15 {hour:02d}:30:00"):
        assert is_bulk_window_now() is expected