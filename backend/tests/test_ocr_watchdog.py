"""OCR 看门狗单测(2026-10-14)。

背景: PaddleOCR-VL(8001) `int(Tensor) static graph` 崩溃后进程内无法自愈,
须重启; 看门狗周期真实探活(小图调 /ocr), 连续失败自动重启, 并按运行页数/
时长阈值做空闲滚动重启。本测试覆盖判定逻辑、重启防抖与故障恢复路径。
"""
import asyncio
import time
from unittest.mock import patch

import pytest

from app.config import settings
from app.core import ocr_watchdog as wd


@pytest.fixture(autouse=True)
def _reset_state():
    wd._FAIL_STREAK = 0
    wd._LAST_RESTART_TS = 0.0
    yield
    wd._FAIL_STREAK = 0
    wd._LAST_RESTART_TS = 0.0


def test_should_roll_pages_threshold():
    assert wd._should_roll({"pages": settings.OCR_WATCHDOG_ROLL_PAGES, "inflight": 0,
                            "uptime_s": 10}) is True


def test_should_roll_uptime_threshold():
    assert wd._should_roll({"pages": 1, "inflight": 0,
                            "uptime_s": settings.OCR_WATCHDOG_ROLL_MINUTES * 60}) is True


def test_should_roll_skips_when_busy():
    stats = {"pages": settings.OCR_WATCHDOG_ROLL_PAGES + 100, "inflight": 1, "uptime_s": 0}
    assert wd._should_roll(stats) is False


def test_should_roll_no_stats():
    assert wd._should_roll(None) is False
    assert wd._should_roll({}) is False


def test_build_launch_cmd_checkout_scoped():
    cmd = wd._build_launch_cmd()
    assert str(wd.PADDLE_VENV_PYTHON) in cmd[0]
    assert "paddle_ocr_service.main:app" in cmd
    assert "--port" in cmd


def test_restart_debounce_skips_second_restart():
    wd._LAST_RESTART_TS = time.time()
    with patch.object(wd, "PADDLE_VENV_PYTHON") as pv:
        pv.exists.return_value = True
        with patch.object(wd.subprocess, "run") as run, \
                patch.object(wd.subprocess, "Popen") as popen:
            ok = wd._restart_service("test")
    assert ok is False
    run.assert_not_called()
    popen.assert_not_called()


def test_restart_runs_own_checkout_kill_and_spawn():
    with patch.object(wd, "PADDLE_VENV_PYTHON") as pv, \
            patch.object(wd, "OCR_LOG_PATH") as lp, \
            patch.object(wd.subprocess, "run") as run, \
            patch.object(wd.subprocess, "Popen") as popen, \
            patch.object(wd.time, "sleep"):
        pv.exists.return_value = True
        pv.__str__ = lambda self: "/x/paddle_venv/bin/python"
        pv.__fspath__ = lambda self: "/x/paddle_venv/bin/python"
        lp.parent.mkdir = lambda **k: None
        # open() 被 mock 掉需要真实文件? open 用内置 —— 改用 patch builtins.open
        with patch("builtins.open", create=True) as op:
            op.return_value = op  # 文件句柄占位
            ok = wd._restart_service("unit-test")
    assert ok is True
    # 优先按端口杀(fuser -k <port>/tcp), 不依赖 argv 路径形态
    assert run.call_count >= 1
    assert "8001/tcp" in " ".join(run.call_args_list[0][0][0])
    popen.assert_called_once()
    assert popen.call_args.kwargs.get("start_new_session") is True


def test_sweep_fail_streak_triggers_restart_and_recovers():
    calls = {"restart": 0}

    async def _probe_fail():
        return False, None

    async def _probe_ok():
        return True, {"pages": 0, "inflight": 0, "uptime_s": 1}

    async def _noop_ready():
        return True

    def _fake_restart(reason):
        calls["restart"] += 1
        return True

    with patch.object(wd, "_probe_once", side_effect=_probe_fail), \
            patch.object(wd, "_restart_service", side_effect=_fake_restart), \
            patch.object(wd, "_wait_ready", side_effect=_noop_ready):
        asyncio.run(wd._sweep_once())          # fail 1/2 → 不重启
        assert calls["restart"] == 0
        asyncio.run(wd._sweep_once())          # fail 2/2 → 重启
        assert calls["restart"] == 1
        assert wd._FAIL_STREAK == 0            # 恢复后清零

    wd._FAIL_STREAK = 0
    with patch.object(wd, "_probe_once", side_effect=_probe_ok):
        asyncio.run(wd._sweep_once())
        assert wd._FAIL_STREAK == 0


def test_sweep_rolling_restart_on_stats():
    calls = {"restart": 0}

    async def _probe_ok():
        return True, {"pages": settings.OCR_WATCHDOG_ROLL_PAGES, "inflight": 0, "uptime_s": 5}

    async def _noop_ready():
        return True

    def _fake_restart(reason):
        calls["restart"] += 1
        assert reason == "rolling"
        return True

    with patch.object(wd, "_probe_once", side_effect=_probe_ok), \
            patch.object(wd, "_restart_service", side_effect=_fake_restart), \
            patch.object(wd, "_wait_ready", side_effect=_noop_ready):
        asyncio.run(wd._sweep_once())
    assert calls["restart"] == 1


def test_start_disabled_returns_immediately():
    with patch.object(settings, "OCR_WATCHDOG_ENABLED", False):
        asyncio.run(wd.start())  # 不应死循环/不应抛错
