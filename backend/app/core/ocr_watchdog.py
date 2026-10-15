"""OCR 服务看门狗(2026-10-14): 深度探活 + 故障自动重启 + 滚动重启。

背景: PaddleOCR-VL(8001) 长跑后偶发 `RuntimeError: int(Tensor) is not
supported in static graph mode`, 进程内无法自愈 —— 此前必须人工重启, 其间
扫描件解析会静默缺页(混合 OCR 失败仅记 warning 不落 failed_stage)。

本看门狗(随 FastAPI 8000 启动, 同 interp_watchdog 模式)周期执行:
  1. **深度探活**: 用内嵌小图真实调用 /ocr(而非 /health 浅检查), 故障即暴露;
  2. **故障重启**: 连续 OCR_WATCHDOG_MAX_FAILS 次失败 → kill 本 checkout 的
     OCR 进程(按 paddle_venv 绝对路径精确匹配, 不误杀其它 checkout)并按
     start.sh 同款命令重启, 等待就绪(模型加载 ~1min);
  3. **滚动重启(兜底)**: 读 /stats, 当累计处理页数 ≥ OCR_WATCHDOG_ROLL_PAGES
     或运行时长 ≥ OCR_WATCHDOG_ROLL_MINUTES 且 inflight==0(空闲窗口) → 主动
     重启 —— "已知会漏、主动换桶", 规避长跑状态退化;
  4. 全量日志(启动/重启/恢复/失败)写 app.log(`app.ocr.watchdog`)。

重启防抖: 两次重启最小间隔 90s, 防探活抖动引发重启风暴。
"""
import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import settings

log = logging.getLogger("app.ocr.watchdog")

BACKEND_DIR = Path(__file__).resolve().parents[2]
PADDLE_VENV_PYTHON = BACKEND_DIR / "paddle_venv" / "bin" / "python"
OCR_LOG_PATH = Path("/data/logs/paddle-ocr.stdout.log")

# 探活小图(200x60 白底黑字 "HEALTH 123" PNG) —— 真实走 layout+VLM 推理,
# 任何"进程活着但推理已坏"(int(Tensor) 类)都会被探出。
PROBE_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAMgAAAA8CAIAAACsOWLGAAADBUlEQVR4nO3bzys0cRzA8a+nbdWmFkVu"
    "LmrnRu1Bk11NNuXCQcpJ6w+QC3+C0rblwElpIjdNfkVZDnIgR7dxUU4risiPMujrMD3a7C/Z/exTT+/X"
    "ab6fxndm6t0OW+q01gqotj//+gbwfyIsiCAsiCAsiCAsiCAsiCAsiCAsiCAsiCAsiCAsiCAsiCAsiCAs"
    "iCAsiCAsiCAsiCgfVmNjY8FlKBSy/pqbm/OHS0tL9fX119fXBX92Y2PDPz8QCPgHjuMU2993f38/Pj4e"
    "Doe/JrZtx+Pxrq6u/f19pdTLy8vo6KhlWdFodHd3t/wTozZ0OeFwuODy29w3NDQ0PT1t23aJc77Ni+3v"
    "i8Vi8/PzX8Obm5ve3t6Pjw/XdQ3D0FqnUql0Oq21zmaz7e3tZR8HtVHNsJ6fnxOJxPn5+fDwcLFz8uel"
    "w7q6usoduq67tramtX56emppadFa393dvb6+aq0PDg46OjrKPg5qI1DFD79MJjMwMBCJRC4vLz3PCwaD"
    "le/Z1taWuzQMwzAMpZTjOIODg0qppqYmpdTY2Nj6+vr29nblV0RVlA/L8zzLsnKX+fPZ2VnTNLe2ts7O"
    "zhzHyWazR0dH/f39P7mDYvuXcHFxkU6nDw8Pvyarq6sjIyMrKyuJROInF4W4sp9pP3wVvr+/m6bpH+/t"
    "7U1OTuafU3DP0q/C/OHj42M0Gj09PfWXExMTb29v/g00NzeXfBTUTtW+bjg+Pu7s7PSP4/G4/ydb1Wmt"
    "k8nk1NRUd3e3P3l4eNjc3FRKnZycRCIRiYviF37/O1buK8w0Tc/z+vr6/GUoFGptbXVd1/O8WCzmD3t6"
    "elKpVGV3q5aXlzOZzO3t7eLiYkNDw87OzszMTDKZXFhYCAaDtm1XuD+qpU7zn9AQwDfvEEFYEEFYEEFY"
    "EEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFY"
    "EEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEEFYEPEJMjcsOGQbTosAAAAASUVORK5CYII="
)

_MIN_RESTART_GAP_S = 90          # 重启防抖: 两次重启最小间隔
_FAIL_STREAK = 0                 # 连续探活失败计数(模块态, 单看门狗协程使用)
_LAST_RESTART_TS = 0.0


def _ocr_base() -> str:
    return (settings.OCR_BASE_URL or "http://localhost:8001").rstrip("/")


def _build_launch_cmd() -> list:
    """与 start.sh 同款启动命令(不含 shell 包装; cwd/env 由调用方设置)。"""
    port = 8001
    try:
        port = urlparse(_ocr_base()).port or 8001
    except Exception:
        pass
    return [
        str(PADDLE_VENV_PYTHON), "-m", "uvicorn",
        "paddle_ocr_service.main:app", "--host", "0.0.0.0", "--port", str(port),
    ]


def _launch_env() -> dict:
    env = dict(os.environ)
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env["CUDA_VISIBLE_DEVICES"] = env.get("OCR_CUDA_DEVICE", "3")
    env.setdefault("PADDLEOCR_VL_MODEL", "/data/models/PaddleOCR-VL-1.5")
    env.setdefault("PP_DOCLAYOUT_MODEL", "/data/models/PP-DocLayoutV2")
    return env


def _port() -> int:
    try:
        return urlparse(_ocr_base()).port or 8001
    except Exception:
        return 8001


def _kill_ocr_process() -> None:
    """kill 占用 OCR 端口的进程。

    优先按**端口**精确匹配(fuser/lsof) —— 不依赖 argv 路径形态(相对/绝对均可),
    也天然只命中本机该端口的服务; 回退时才用 cmdline 匹配(绝对+相对两种形态)。
    """
    port = _port()
    killed = False
    for cmd in (["fuser", "-k", f"{port}/tcp"],
                ["bash", "-c", f"lsof -ti tcp:{port} | xargs -r kill"]):
        try:
            r = subprocess.run(cmd, check=False, capture_output=True)
            if r.returncode == 0:
                killed = True
                break
        except Exception:
            continue
    if not killed:
        # 回退: 绝对路径形态(start.sh 启动) + 相对路径形态(手工 ./paddle_venv 启动)
        for pattern in (f"{PADDLE_VENV_PYTHON} -m uvicorn paddle_ocr_service.main:app",
                        "paddle_venv/bin/python -m uvicorn paddle_ocr_service.main:app"):
            subprocess.run(["pkill", "-f", pattern], check=False)


async def _probe_once() -> tuple:
    """返回 (ok, stats)。ok=POST /ocr 200; stats=/stats JSON(失败为 None)。

    首次请求会触发模型懒加载(可达 ~2min) —— read 超时放宽到 180s。
    """
    timeout = httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{_ocr_base()}/ocr", json={"image_base64": PROBE_IMAGE_B64})
            ok = r.status_code == 200
            stats = None
            try:
                rs = await c.get(f"{_ocr_base()}/stats")
                if rs.status_code == 200:
                    stats = rs.json()
            except Exception:
                stats = None
            return ok, stats
    except Exception as e:
        log.warning("ocr probe failed: %s", e)
        return False, None


def _should_roll(stats: Optional[dict]) -> bool:
    """滚动重启判定: 页数/时长超阈值 且 空闲(inflight==0)。stats 缺失返回 False。"""
    if not stats:
        return False
    try:
        if int(stats.get("inflight") or 0) > 0:
            return False
        if int(stats.get("pages") or 0) >= settings.OCR_WATCHDOG_ROLL_PAGES:
            return True
        if int(stats.get("uptime_s") or 0) >= settings.OCR_WATCHDOG_ROLL_MINUTES * 60:
            return True
    except Exception:
        return False
    return False


def _restart_service(reason: str) -> bool:
    """kill + 重启 OCR 服务。同步执行(在 executor 里跑), 返回是否成功拉起。"""
    global _LAST_RESTART_TS
    now = time.time()
    if now - _LAST_RESTART_TS < _MIN_RESTART_GAP_S:
        log.warning("ocr watchdog restart skipped (debounce, reason=%s)", reason)
        return False
    _LAST_RESTART_TS = now
    if not PADDLE_VENV_PYTHON.exists():
        log.error("ocr watchdog cannot restart: venv python missing %s", PADDLE_VENV_PYTHON)
        return False
    log.warning("ocr watchdog restarting service (reason=%s)", reason)
    _kill_ocr_process()
    time.sleep(2)
    try:
        OCR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(OCR_LOG_PATH, "ab")
        try:
            subprocess.Popen(
                _build_launch_cmd(), cwd=str(BACKEND_DIR), env=_launch_env(),
                stdout=log_fh, stderr=subprocess.STDOUT,
                start_new_session=True, close_fds=True,
            )
        finally:
            log_fh.close()
    except Exception:
        log.exception("ocr watchdog spawn failed")
        return False
    return True


async def _wait_ready() -> bool:
    """重启后等待就绪(模型加载可达 ~1min)。"""
    deadline = time.time() + settings.OCR_WATCHDOG_RESTART_TIMEOUT
    while time.time() < deadline:
        ok, _stats = await _probe_once()
        if ok:
            log.warning("ocr watchdog service recovered")
            return True
        await asyncio.sleep(8)
    log.error("ocr watchdog service NOT ready within %ss", settings.OCR_WATCHDOG_RESTART_TIMEOUT)
    return False


async def _sweep_once() -> None:
    """一个巡检周期: 探活 → (失败累计到阈值则重启) → 滚动重启判定。"""
    global _FAIL_STREAK
    ok, stats = await _probe_once()
    if ok:
        _FAIL_STREAK = 0
        if _should_roll(stats):
            log.warning("ocr watchdog rolling restart (stats=%s)", stats)
            loop = asyncio.get_running_loop()
            restarted = await loop.run_in_executor(None, _restart_service, "rolling")
            if restarted:
                await _wait_ready()
        return
    _FAIL_STREAK += 1
    log.warning("ocr watchdog probe failed streak=%s/%s", _FAIL_STREAK,
                settings.OCR_WATCHDOG_MAX_FAILS)
    if _FAIL_STREAK < settings.OCR_WATCHDOG_MAX_FAILS:
        return
    loop = asyncio.get_running_loop()
    restarted = await loop.run_in_executor(None, _restart_service, f"probe failed x{_FAIL_STREAK}")
    if restarted:
        if await _wait_ready():
            _FAIL_STREAK = 0


async def start():
    """后台协程: 周期深度探活 + 自动重启。"""
    if not settings.OCR_WATCHDOG_ENABLED:
        log.info("ocr watchdog disabled")
        return
    # 首次巡检提前到 15s(服务刚起时快速确认; 之后按 interval)
    first = True
    while True:
        try:
            await asyncio.sleep(15 if first else settings.OCR_WATCHDOG_INTERVAL)
            first = False
            await _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("ocr watchdog sweep error: %s", e)
