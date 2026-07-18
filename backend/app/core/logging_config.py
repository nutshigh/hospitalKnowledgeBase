"""日志收口配置。

所有 Python 进程入口调用 ``setup_logging()`` 一次,把日志统一写到
``/data/logs/app.log``;按月初切分,旧文件 rename 为 ``app.log.<YYYY-MM>``,
``backupCount=0`` 表示永久保留(运维人工清理)。

纯 stdlib 实现,不引入第三方日志库;主 venv / vLLM venv / paddle venv 的
隔离关系不受影响(只有主 venv 进程会 import 本模块)。
"""
import logging
import os
import time
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any

LOG_DIR = "/data/logs"
LOG_FILE = os.path.join(LOG_DIR, "app.log")
# 永久保留历史月日志(运维人工清理);backupCount=0 在 TimedRotatingFileHandler
# 语义里就是「不删除任何备份文件」。
_BACKUP_COUNT = 0
_FMT = "%(asctime)s | %(levelname)s:%(name)s:%(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _now_ts() -> float:
    return time.time()


class MonthlyRotatingFileHandler(TimedRotatingFileHandler):
    """按 calendar month 切分的 file handler。

    继承 stdlib 的 TimedRotatingFileHandler,但把 rollover 触发条件改为
    「当前时间所在月的下一个月初 1 号 0:00 已过」;suffix 固定为
    ``"%Y-%m"`` 以便运维 grep。backupCount=0 表示永久保留。
    """

    def __init__(self, filename: str, **kwargs: Any) -> None:
        super().__init__(
            filename=filename,
            when="MIDNIGHT",  # 占位;实际切分点由 computeRollover 决定
            interval=1,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            utc=False,
            **kwargs,
        )
        # suffix 格式覆盖父类默认的 "%Y-%m-%d" → "%Y-%m"
        self.suffix = "%Y-%m"

    def computeRollover(self, currentTime: float) -> float:
        """返回「当前时间所在月的下一个月初 1 号 0:00」的 POSIX timestamp。

        与父类不同,我们不按 interval 累加,而是直接对齐到 next month start,
        避免日级 MIDNIGHT 累加在月末跳月时出错。
        """
        t = datetime.fromtimestamp(currentTime)  # local tz
        year = t.year + (1 if t.month == 12 else 0)
        month = 1 if t.month == 12 else t.month + 1
        first_of_next = t.replace(year=year, month=month, day=1,
                                   hour=0, minute=0, second=0, microsecond=0)
        return first_of_next.timestamp()

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        """当前时间是否已越过上次计算的下月起点。"""
        current = _now_ts()
        if self.rolloverAt is None or self.rolloverAt < 0:
            self.rolloverAt = self.computeRollover(current)
        if current >= self.rolloverAt:
            return True
        if os.path.exists(self.baseFilename):
            return False
        return True

    def doRollover(self) -> None:
        """切分时若 rename 失败(目标已存在/权限错/磁盘错),记一条 WARNING
        到当前 handler 并继续 append,不丢失日志、不抛;下个月切分再重试。"""
        try:
            super().doRollover()
        except OSError as e:
            logging.getLogger("app.core.logging_config").warning(
                "monthly rollover failed for %s (continue appending): %r",
                self.baseFilename, e,
            )


def setup_logging(default_level: str = "INFO") -> None:
    """在每个 Python 进程入口调用一次。

    - 优先读 ``os.environ["LOG_LEVEL"]``,否则用 ``default_level``
    - mkdir -p ``LOG_DIR`` (mode=0o775)
    - 给 root logger 装一个 MonthlyRotatingFileHandler,Formatter 含时间戳
    - 失败不抛,回退 StreamHandler 到 stdout,确保进程能启动
    """
    level_name = os.environ.get("LOG_LEVEL", default_level).upper()
    level = getattr(logging, level_name, logging.INFO)

    try:
        os.makedirs(LOG_DIR, mode=0o775, exist_ok=True)
        handler: logging.Handler = MonthlyRotatingFileHandler(LOG_FILE)
    except (PermissionError, OSError) as e:
        # 回退到 stdout,不让日志初始化吞掉进程启动
        import sys
        print(f"[logging_config] cannot open {LOG_FILE}: {e!r}; fallback to stdout",
              flush=True)
        handler = logging.StreamHandler(sys.stdout)

    handler.setFormatter(logging.Formatter(_FMT, _DATEFMT))

    root = logging.getLogger()
    # 清掉默认/上次设置的 handlers,避免线程里被多次装饰
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)