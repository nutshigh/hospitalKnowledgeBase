"""PaddleOCR-VL-1.5 HTTP 服务。

封装 PaddleOCRVL pipeline 为 HTTP 接口，供 backend/app/core/vlm_client.py 调用。
端口 8001（复用原 OCR 端口），GPU3。

接口：
  GET  /health          -> {"status":"ok"}
  POST /ocr             -> {"markdown": "...", "json": {...}}
       body: {"image_base64": "<base64 jpeg/png>"}
       或   {"image_path": "/path/to/image"}
"""
import base64
import io
import os
import tempfile
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

app = FastAPI(title="PaddleOCR-VL Service")

_pipeline = None


def _get_pipeline():
    """懒加载 PaddleOCRVL pipeline（首次请求时加载，避免启动时长时间阻塞）。"""
    global _pipeline
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

        output = pipeline.predict(img_path)
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

        return OcrResponse(markdown=markdown_text.strip(), raw_json=json_data)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
