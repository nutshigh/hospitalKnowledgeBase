import logging
import os
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from app.core.logging_config import MonthlyRotatingFileHandler, setup_logging


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_monthly_rollover_renames_to_yyyymm_and_starts_new_file(tmp_path):
    """跨月初一次 rollover:旧文件 rename 为 app.log.<YYYY-MM>,新 app.log 为空等待新写入。"""
    log_file = tmp_path / "app.log"
    handler = MonthlyRotatingFileHandler(str(log_file))
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("test_monthly_rollover")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with freeze_time("2026-07-31 23:59:00", ignore=["transformers"]):
        logger.info("july-line-1")
    handler.flush()

    # 触发月初 rollover(shouldRollover 检查 record 时间)
    with freeze_time("2026-08-01 00:00:30", ignore=["transformers"]):
        logger.info("august-line-1")
    handler.flush()
    handler.close()

    rotated = tmp_path / "app.log.2026-07"
    assert rotated.exists(), "旧月文件应 rename 为 app.log.2026-07"
    assert "july-line-1" in _read(rotated)
    assert "august-line-1" in _read(log_file), "新月第一行应写入新 app.log"


def test_setup_logging_creates_dir_and_writes_to_app_log(tmp_path, monkeypatch):
    """setup_logging 把 root handler 装到 LOG_FILE,并 mkdir -p LOG_DIR。"""
    log_dir = tmp_path / "logs"
    log_file = log_dir / "app.log"
    monkeypatch.setattr("app.core.logging_config.LOG_DIR", str(log_dir))
    monkeypatch.setattr("app.core.logging_config.LOG_FILE", str(log_file))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    # 清掉 root handlers,避免 test 间污染
    root = logging.getLogger()
    root.handlers.clear()

    setup_logging("INFO")

    assert log_dir.exists(), "/data/logs 目录应被创建"
    logging.getLogger("probe.logger1").debug("debug-line")
    logging.getLogger("probe.logger1").info("info-line")
    # flush 一下
    for h in root.handlers:
        h.flush()

    content = _read(log_file)
    assert "info-line" in content
    assert "debug-line" in content, "LOG_LEVEL=DEBUG 时 root 应捕获 debug"
    assert " | " in content and "probe.logger1" in content

    # 清理:把 root 还原成空 handlers,避免污染其它测试
    root.handlers.clear()
    root.addHandler(logging.NullHandler())


def test_setup_logging_respects_warning_level(tmp_path, monkeypatch):
    """LOG_LEVEL=WARNING 时 debug/info 不应写入文件。"""
    log_dir = tmp_path / "logs"
    log_file = log_dir / "app.log"
    monkeypatch.setattr("app.core.logging_config.LOG_DIR", str(log_dir))
    monkeypatch.setattr("app.core.logging_config.LOG_FILE", str(log_file))
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    root = logging.getLogger()
    root.handlers.clear()

    setup_logging("INFO")  # default 被 env 覆盖为 WARNING
    logging.getLogger("probe2").debug("debug-line-2")
    logging.getLogger("probe2").info("info-line-2")
    logging.getLogger("probe2").warning("warn-line-2")
    for h in root.handlers:
        h.flush()

    content = _read(log_file)
    assert "warn-line-2" in content
    assert "debug-line-2" not in content
    assert "info-line-2" not in content

    root.handlers.clear()
    root.addHandler(logging.NullHandler())