import os
from datetime import datetime

BACKOFFS_MS = (10_000, 60_000, 600_000)  # 10s, 1m, 10m


def backoff_for_retry(retry_count: int) -> int:
    """返回下轮重试前等待 ms。retry_count 是已失败次数(0 表示第一次失败)。"""
    idx = min(retry_count, len(BACKOFFS_MS) - 1)
    return BACKOFFS_MS[idx]


def is_bulk_window_now() -> bool:
    """当前是否处于 bulk 允许消费时段。直接读 os.getenv 让 monkeypatch.setenv 生效。"""
    start = int(os.getenv("BULK_WINDOW_START", "22"))
    end = int(os.getenv("BULK_WINDOW_END", "8"))
    h = datetime.now().hour
    if start <= end:
        return start <= h < end
    return h >= start or h < end