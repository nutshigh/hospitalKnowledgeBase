"""PaddleOCR-VL-1.5 HTTP 服务。

封装 PaddleOCRVL pipeline 为 HTTP 接口，供 backend/app/core/vlm_client.py 调用。
端口 8001（复用原 OCR 端口），GPU3。

接口：
  GET  /health          -> {"status":"ok"}          （浅检查，不碰模型；启动等待用）
  GET  /stats           -> 页数/失败数/在途数/uptime   （看门狗滚动重启判定用）
  POST /ocr             -> {"markdown": "...", "json": {...}}
       body: {"image_base64": "<base64 jpeg/png>"}
        或   {"image_path": "/path/to/image"}

2026-10-14 稳定性加固：
  - `threading.Lock` 串行化 `pipeline.predict`：Paddle 推理管线非线程安全，
    多 worker 并发请求可能造成内部状态损坏（长跑必崩的可疑诱因之一）。
  - 推理异常显式 `logger.exception` 打完整 traceback（stdout 常驻日志保留，
    不再只靠 uvicorn 默认输出）。
  - /stats 供 OCR 看门狗做"页数/时长阈值 + 空闲窗口"滚动重启。
"""
import base64
import io
import logging
import os
import tempfile
import threading
import time
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("paddle_ocr_service")

app = FastAPI(title="PaddleOCR-VL Service")

_pipeline = None
_pipeline_lock = threading.Lock()   # 保护懒加载
_predict_lock = threading.Lock()    # 串行化推理(管线非线程安全)
_stats_lock = threading.Lock()
_stats = {"pages": 0, "failed": 0, "inflight": 0}
_started_at = time.time()


def _get_pipeline():
    """懒加载 PaddleOCRVL pipeline（首次请求时加载，避免启动时长时间阻塞）。"""
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                from paddleocr import PaddleOCRVL

                model_dir = os.environ.get("PADDLEOCR_VL_MODEL", "/data/models/PaddleOCR-VL-1.5")
                layout_dir = os.environ.get("PP_DOCLAYOUT_MODEL", "/data/models/PP-DocLayoutV2")
                kwargs = {
                    "pipeline_version": "v1.5",
                }
                # 指向本地权重目录（避免联网下载）
                if os.path.isdir(model_dir):
                    kwargs["vl_rec_model_dir"] = model_dir
                if os.path.isdir(layout_dir):
                    kwargs["layout_detection_model_dir"] = layout_dir
                    kwargs["layout_detection_model_name"] = "PP-DocLayoutV2"
                _pipeline = PaddleOCRVL(**kwargs)
    return _pipeline


class OcrRequest(BaseModel):
    image_base64: Optional[str] = None
    image_path: Optional[str] = None


class OcrResponse(BaseModel):
    markdown: str
    raw_json: Optional[dict] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    with _stats_lock:
        snap = dict(_stats)
    snap["uptime_s"] = int(time.time() - _started_at)
    snap["model_loaded"] = _pipeline is not None
    return snap


@app.post("/ocr", response_model=OcrResponse)
def ocr(req: OcrRequest):
    pipeline = _get_pipeline()

    tmp_path = None
    try:
        if req.image_path:
            img_path = req.image_path
        elif req.image_base64:
            # 写临时文件（PaddleOCRVL.predict 接受文件路径）
            suffix = ".jpg"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(base64.b64decode(req.image_base64))
            img_path = tmp_path
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="image_base64 or image_path required")

        with _stats_lock:
            _stats["inflight"] += 1
        try:
            # 串行化: Paddle pipeline 并发调用不安全(状态损坏类崩溃的规避)
            with _predict_lock:
                output = pipeline.predict(img_path)
        except Exception:
            with _stats_lock:
                _stats["failed"] += 1
            logger.exception("predict failed image=%s", img_path)
            raise
        finally:
            with _stats_lock:
                _stats["inflight"] -= 1

        markdown_text = ""
        json_data = None
        for res in output:
            # PaddleOCRVLResult.markdown 是 dict: {'markdown_texts': str, 'markdown_images': {...}, ...}
            md = getattr(res, "markdown", None)
            if md:
                if isinstance(md, dict):
                    markdown_text += md.get("markdown_texts", "") + "\n"
                elif isinstance(md, str):
                    markdown_text += md + "\n"
            # json 表示
            try:
                json_data = res.json if hasattr(res, "json") else (
                    res.to_dict() if hasattr(res, "to_dict") else None
                )
            except Exception:
                json_data = None

        with _stats_lock:
            _stats["pages"] += 1
        return OcrResponse(markdown=markdown_text.strip(), raw_json=json_data)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
