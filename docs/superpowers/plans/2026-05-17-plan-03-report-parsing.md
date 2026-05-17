# 报告解析模块 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现报告解析模块——文件上传、图像预处理、多格式解析（PDF/Word/图片）、VLM 结构化提取、术语标准化、异步任务状态机。

**Architecture:** 报告解析模块是数据链路起点。用户上传文件后，创建任务入 RabbitMQ 队列，Worker 消费完成预处理→VLM 识别→术语标准化→结构化存储，最后发布"解析完成"事件触发 AI 解读。状态机保证任务可追踪。

**Tech Stack:** FastAPI, Pillow+OpenCV, PyMuPDF, RabbitMQ, 本地 VLM（Qwen-VL 兼容 API）

---

## 文件结构

```
backend/app/
├── modules/
│   └── report/
│       ├── __init__.py
│       ├── models.py           # SQLAlchemy 模型（report_task, report_info, report_indicator）
│       ├── schemas.py          # Pydantic 请求/响应
│       ├── service.py          # 业务逻辑
│       ├── router.py           # 上传 + 查询 API
│       └── worker.py           # RabbitMQ Worker（消费解析任务）
├── core/
│   ├── image_preprocess.py     # 图像预处理（模糊检测/裁剪/校正）
│   ├── vlm_client.py           # VLM 调用客户端
│   └── term_normalizer.py      # 术语标准化
└── main.py                     # 注册 report 路由
```

---

### Task 1: 创建分支 + ORM 模型

**Branch:** 从 `infra-setup` 切出 `feat/report-parsing`

- [ ] **Step 1: 创建分支**

```bash
git checkout infra-setup
git checkout -b feat/report-parsing
```

- [ ] **Step 2: 编写 ORM 模型**

`app/modules/report/models.py`:
```python
from sqlalchemy import Column, BigInteger, String, Text, Integer, Date, DateTime, ForeignKey, func
from app.models.base import Base


class ReportTask(Base):
    __tablename__ = "report_task"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    original_file_path = Column(String(500), nullable=False)
    original_filename = Column(String(200), nullable=False)
    file_type = Column(String(10), nullable=False)
    file_size = Column(BigInteger, nullable=False, default=0)
    thumbnail_path = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="queued")
    priority = Column(Integer, nullable=False, default=0)
    retry_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)


class ReportInfo(Base):
    __tablename__ = "report_info"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(BigInteger, ForeignKey("report_task.id"), nullable=True)
    user_id = Column(BigInteger, nullable=False)
    name = Column(String(50), nullable=True)
    gender = Column(String(5), nullable=True)
    age = Column(Integer, nullable=True)
    report_date = Column(Date, nullable=True)
    check_type = Column(String(20), nullable=True)
    unit_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class ReportIndicator(Base):
    __tablename__ = "report_indicator"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, ForeignKey("report_info.id"), nullable=False)
    item_name = Column(String(100), nullable=False)
    item_name_standard = Column(String(100), nullable=True)
    item_code = Column(String(50), nullable=True)
    result_value = Column(String(50), nullable=True)
    unit = Column(String(20), nullable=True)
    ref_range_low = Column(String(50), nullable=True)
    ref_range_high = Column(String(50), nullable=True)
    category = Column(String(50), nullable=True)
    raw_text = Column(Text, nullable=True)
```

- [ ] **Step 3: 验证导入**

```bash
uv run python -c "from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/report/
git commit -m "feat(report): add ORM models (task, info, indicator)"
```

---

### Task 2: Pydantic Schemas

- [ ] **Step 1: 编写 schemas.py**

`app/modules/report/schemas.py`:
```python
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date


class ReportIndicatorSchema(BaseModel):
    item_name: str
    item_name_standard: Optional[str] = None
    item_code: Optional[str] = None
    result_value: Optional[str] = None
    unit: Optional[str] = None
    ref_range_low: Optional[str] = None
    ref_range_high: Optional[str] = None
    category: Optional[str] = None


class ReportInfoSchema(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    report_date: Optional[date] = None
    check_type: Optional[str] = None
    unit_name: Optional[str] = None
    indicators: List[ReportIndicatorSchema] = []


class TaskStatusResponse(BaseModel):
    task_id: int
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class ReportListResponse(BaseModel):
    items: List[dict]
    total: int
    page: int
    page_size: int


class ReportDetailResponse(BaseModel):
    id: int
    task_id: Optional[int] = None
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    report_date: Optional[date] = None
    check_type: Optional[str] = None
    unit_name: Optional[str] = None
    indicators: List[ReportIndicatorSchema] = []
    created_at: datetime
```

- [ ] **Step 2: Commit**

```bash
git add app/modules/report/schemas.py
git commit -m "feat(report): add Pydantic schemas"
```

---

### Task 3: 图像预处理

**Install:** `uv add opencv-python-headless Pillow`

- [ ] **Step 1: 安装依赖**

```bash
uv add opencv-python-headless Pillow
```

- [ ] **Step 2: 编写 image_preprocess.py**

`app/core/image_preprocess.py`:
```python
import cv2
import numpy as np
from PIL import Image
from typing import Tuple, Optional
import os


def detect_blur(image_path: str, threshold: float = 100.0) -> bool:
    """Return True if image is too blurry."""
    img = cv2.imread(image_path)
    if img is None:
        return True
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    return laplacian_var < threshold


def auto_crop(image_path: str, output_path: str) -> str:
    """Detect document edges and crop. Returns output path."""
    img = cv2.imread(image_path)
    if img is None:
        return image_path
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 75, 200)
    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image_path
    largest = max(contours, key=cv2.contourArea)
    epsilon = 0.02 * cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, epsilon, True)
    if len(approx) == 4:
        pts = _order_points(approx.reshape(4, 2))
        warped = _four_point_transform(img, pts)
        cv2.imwrite(output_path, warped)
        return output_path
    return image_path


def correct_skew(image_path: str, output_path: str) -> str:
    """Detect and correct skew angle. Returns output path."""
    img = cv2.imread(image_path)
    if img is None:
        return image_path
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10)
    if lines is None:
        return image_path
    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi
        angles.append(angle)
    if not angles:
        return image_path
    median_angle = np.median(angles)
    if abs(median_angle) < 0.5:
        return image_path
    h, w = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
    rotated = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    cv2.imwrite(output_path, rotated)
    return output_path


def generate_thumbnail(image_path: str, output_path: str, size: Tuple[int, int] = (300, 400)):
    img = Image.open(image_path)
    img.thumbnail(size, Image.LANCZOS)
    img.save(output_path)


def preprocess(image_path: str, output_dir: str) -> Tuple[str, Optional[str]]:
    """
    Run full preprocessing pipeline. Returns (processed_path, error_message).
    Error message is None on success.
    """
    if detect_blur(image_path):
        return image_path, "照片模糊，请重新拍摄"

    basename = os.path.splitext(os.path.basename(image_path))[0]
    crop_path = os.path.join(output_dir, f"{basename}_crop.jpg")
    skew_path = os.path.join(output_dir, f"{basename}_skew.jpg")
    thumb_path = os.path.join(output_dir, f"{basename}_thumb.jpg")

    processed = auto_crop(image_path, crop_path)
    processed = correct_skew(processed, skew_path)
    generate_thumbnail(processed, thumb_path)

    return processed, None


def _order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _four_point_transform(image, pts):
    rect = _order_points(pts)
    (tl, tr, br, bl) = rect
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))
    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))
    dst = np.array([[0, 0], [maxWidth - 1, 0], [maxWidth - 1, maxHeight - 1], [0, maxHeight - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, M, (maxWidth, maxHeight))
```

- [ ] **Step 3: 验证导入**

```bash
uv run python -c "from app.core.image_preprocess import preprocess, detect_blur; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app/core/image_preprocess.py pyproject.toml uv.lock
git commit -m "feat(report): add image preprocessing (blur/crop/skew/thumbnail)"
```

---

### Task 4: VLM 客户端

- [ ] **Step 1: 编写 vlm_client.py**

`app/core/vlm_client.py`:
```python
import json
from typing import Optional
from httpx import Client, Timeout


SYSTEM_PROMPT = """你是体检报告结构化提取助手。从提供的体检报告图片中精确提取信息。
遵守以下规则:
1. 个人信息（姓名、性别、年龄、检查日期）尽可能提取
2. 每个检验项精确提取: 项目名称、结果值、单位、参考区间
3. 不遗漏任何检验项
4. 无法识别的内容标记为 null
5. 仅返回 JSON，不包含任何解释文字

输出格式:
{
  "personal_info": { "name": null, "gender": null, "age": null, "check_date": null },
  "indicators": [
    { "item_name": "...", "result": "...", "unit": "...", "ref_low": "...", "ref_high": "..." }
  ]
}"""


class VLMClient:
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.client = Client(timeout=Timeout(120.0))

    def extract_from_image(self, image_base64: str) -> dict:
        """Extract structured data from a single image using VLM."""
        response = self.client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": "qwen-vl",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": "请提取这份体检报告的信息", "images": [image_base64]},
                ],
                "format": "json",
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return json.loads(data["message"]["content"])

    def extract_from_images(self, images_base64: list[str]) -> dict:
        """Extract from multiple images (multi-page PDF) and merge results."""
        all_indicators = []
        personal_info = {}
        for img in images_base64:
            result = self.extract_from_image(img)
            if result.get("personal_info"):
                personal_info = {k: v for k, v in result["personal_info"].items() if v is not None}
            if result.get("indicators"):
                all_indicators.extend(result["indicators"])
        return {"personal_info": personal_info, "indicators": all_indicators}


vlm_client = VLMClient()
```

- [ ] **Step 2: 验证导入**

```bash
uv run python -c "from app.core.vlm_client import vlm_client; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add app/core/vlm_client.py
git commit -m "feat(report): add VLM client for structured extraction"
```

---

### Task 5: 术语标准化

- [ ] **Step 1: 编写 term_normalizer.py**

`app/core/term_normalizer.py`:
```python
from typing import Optional, Dict

# Built-in standardization mappings (expandable via DB later)
_STANDARD_MAP: Dict[str, str] = {
    "血糖": "空腹血糖（GLU）",
    "葡萄糖": "空腹血糖（GLU）",
    "糖化血红蛋白": "糖化血红蛋白（HbA1c）",
    "总胆固醇": "总胆固醇（TC）",
    "甘油三酯": "甘油三酯（TG）",
    "高密度脂蛋白": "高密度脂蛋白胆固醇（HDL-C）",
    "低密度脂蛋白": "低密度脂蛋白胆固醇（LDL-C）",
    "谷丙转氨酶": "丙氨酸氨基转移酶（ALT）",
    "谷草转氨酶": "天门冬氨酸氨基转移酶（AST）",
    "尿酸": "尿酸（UA）",
    "肌酐": "肌酐（Cr）",
    "尿素氮": "尿素氮（BUN）",
    "白细胞": "白细胞计数（WBC）",
    "红细胞": "红细胞计数（RBC）",
    "血红蛋白": "血红蛋白（Hb）",
    "血小板": "血小板计数（PLT）",
}


def normalize_item_name(raw_name: str) -> tuple[str, Optional[str]]:
    """Return (standardized_name, item_code). item_code is None when no mapping found."""
    cleaned = raw_name.strip().replace(" ", "").replace("　", "")
    for alias, standard in _STANDARD_MAP.items():
        if alias in cleaned:
            return standard, None
    return raw_name.strip(), None


def normalize_indicators(indicators: list[dict]) -> list[dict]:
    """Normalize all indicator names in a list."""
    for ind in indicators:
        name, code = normalize_item_name(ind.get("item_name", ""))
        ind["item_name_standard"] = name
        ind["item_code"] = code
    return indicators
```

- [ ] **Step 2: 验证导入**

```bash
uv run python -c "from app.core.term_normalizer import normalize_item_name; print(normalize_item_name('血糖'))"
```

Expected: `('空腹血糖（GLU）', None)`

- [ ] **Step 3: Commit**

```bash
git add app/core/term_normalizer.py
git commit -m "feat(report): add medical term normalizer"
```

---

### Task 6: 业务逻辑层 + Worker

- [ ] **Step 1: 编写 service.py**

`app/modules/report/service.py`:
```python
import base64
import os
from typing import Optional, List
from sqlalchemy.orm import Session

from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator
from app.core.vlm_client import vlm_client
from app.core.term_normalizer import normalize_indicators
from app.core.image_preprocess import preprocess
from app.core.rabbitmq import rabbitmq, TaskMessage
from app.config import settings


# ---- Upload ----

def create_task(db: Session, hospital_id: str, user_id: int, file_path: str,
                filename: str, file_type: str, file_size: int,
                thumbnail_path: Optional[str] = None,
                priority: int = 0) -> ReportTask:
    task = ReportTask(
        user_id=user_id, original_file_path=file_path, original_filename=filename,
        file_type=file_type, file_size=file_size, thumbnail_path=thumbnail_path,
        status="queued", priority=priority,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    rabbitmq.publish(TaskMessage(
        task_type="parsing", hospital_id=hospital_id, priority=priority,
        payload={"task_id": task.id, "hospital_id": hospital_id, "file_path": file_path},
    ))
    return task


def get_task_status(db: Session, task_id: int) -> Optional[ReportTask]:
    return db.query(ReportTask).filter(ReportTask.id == task_id).first()


# ---- Parsing (Worker) ----

def process_task(db: Session, task_id: int, hospital_id: str):
    """Called by Worker. Full processing pipeline."""
    task = get_task_status(db, task_id)
    if not task:
        return

    task.status = "parsing"
    db.commit()

    try:
        user_dir = os.path.dirname(task.original_file_path)

        # Preprocess images
        if task.file_type == "image":
            processed_path, error_msg = preprocess(task.original_file_path, user_dir)
            if error_msg:
                task.status = "failed"
                task.error_message = error_msg
                db.commit()
                return
        else:
            processed_path = task.original_file_path

        # Convert to base64 images for VLM
        images_b64 = _file_to_base64_list(processed_path, task.file_type)

        # VLM extraction
        result = vlm_client.extract_from_images(images_b64)
        indicators = normalize_indicators(result.get("indicators", []))
        personal_info = result.get("personal_info", {})

        # Save to DB
        report = ReportInfo(
            task_id=task.id, user_id=task.user_id,
            name=personal_info.get("name"),
            gender=personal_info.get("gender"),
            age=personal_info.get("age"),
            report_date=personal_info.get("check_date"),
            check_type=personal_info.get("check_type"),
            unit_name=personal_info.get("unit_name"),
        )
        db.add(report)
        db.commit()
        db.refresh(report)

        for ind in indicators:
            db.add(ReportIndicator(
                report_id=report.id,
                item_name=ind.get("item_name", ""),
                item_name_standard=ind.get("item_name_standard"),
                item_code=ind.get("item_code"),
                result_value=ind.get("result"),
                unit=ind.get("unit"),
                ref_range_low=ind.get("ref_low"),
                ref_range_high=ind.get("ref_high"),
                raw_text=ind.get("raw_text"),
            ))
        db.commit()

        task.status = "completed"
        task.completed_at = __import__("datetime").datetime.utcnow()
        db.commit()

        # Publish completion event
        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=hospital_id, priority=task.priority,
            payload={"report_id": report.id, "hospital_id": hospital_id},
        ))

    except Exception as e:
        task.retry_count += 1
        task.status = "failed" if task.retry_count >= 3 else "queued"
        task.error_message = str(e)
        db.commit()


def _file_to_base64_list(file_path: str, file_type: str) -> list[str]:
    """Convert a file to a list of base64-encoded images."""
    if file_type in ("image",):
        with open(file_path, "rb") as f:
            return [base64.b64encode(f.read()).decode()]
    elif file_type == "pdf":
        import fitz
        doc = fitz.open(file_path)
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            images.append(base64.b64encode(pix.tobytes("jpg")).decode())
        doc.close()
        return images
    else:
        raise ValueError(f"Cannot convert file_type={file_type} to images")


# ---- Query ----

def list_reports(db: Session, hospital_id: str, user_id: Optional[int] = None,
                 page: int = 1, page_size: int = 20) -> tuple[List[ReportInfo], int]:
    q = db.query(ReportInfo)
    if user_id:
        q = q.filter(ReportInfo.user_id == user_id)
    total = q.count()
    items = q.order_by(ReportInfo.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return items, total


def get_report_detail(db: Session, report_id: int) -> Optional[ReportInfo]:
    return db.query(ReportInfo).filter(ReportInfo.id == report_id).first()


def get_report_indicators(db: Session, report_id: int) -> List[ReportIndicator]:
    return db.query(ReportIndicator).filter(ReportIndicator.report_id == report_id).all()
```

- [ ] **Step 2: 编写 worker.py**

`app/modules/report/worker.py`:
```python
import json
from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq
from app.modules.report.service import process_task


def handle_parsing_task(message: dict):
    """Callback for parsing queue messages."""
    payload = message.get("payload", {})
    task_id = payload.get("task_id")
    hospital_id = payload.get("hospital_id")

    db = next(get_hospital_db(hospital_id))
    try:
        process_task(db, task_id, hospital_id)
    finally:
        db.close()


def start_worker():
    """Start consuming from parsing queues."""
    rabbitmq.consume("parsing.urgent", handle_parsing_task)
    rabbitmq.consume("parsing.normal", handle_parsing_task)
    print("Report parsing worker started, waiting for tasks...")
    rabbitmq.start_consuming()
```

- [ ] **Step 3: 验证导入**

```bash
uv run python -c "from app.modules.report.service import create_task, process_task; from app.modules.report.worker import start_worker; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add app/modules/report/service.py app/modules/report/worker.py
git commit -m "feat(report): add business logic service and RabbitMQ worker"
```

---

### Task 7: REST API 路由

- [ ] **Step 1: 编写 router.py**

`app/modules/report/router.py`:
```python
import os
import uuid
from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_hospital_db
from app.middleware.hospital_context import get_current_hospital_id
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.report import schemas, service
from app.config import settings

router = APIRouter()

ALLOWED_TYPES = {"pdf": "pdf", "docx": "docx", "doc": "docx",
                 "jpg": "image", "jpeg": "image", "png": "image"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


def _get_hospital_id() -> str:
    hid = get_current_hospital_id()
    if not hid:
        raise ValidationException(detail="Hospital context required")
    return hid


def _get_db(hospital_id: str = Depends(_get_hospital_id)):
    return next(get_hospital_db(hospital_id))


@router.post("/upload")
def upload_report(
    file: UploadFile = File(...),
    hospital_id: str = Depends(_get_hospital_id),
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
    file_type = ALLOWED_TYPES.get(ext)
    if not file_type:
        raise ValidationException(detail=f"Unsupported format. Allowed: {list(ALLOWED_TYPES.keys())}")

    # Save file
    storage_dir = os.path.join(settings.FILE_STORAGE_ROOT, hospital_id, "reports", str(current_user.user_id))
    os.makedirs(storage_dir, exist_ok=True)
    file_id = uuid.uuid4().hex
    file_path = os.path.join(storage_dir, f"{file_id}.{ext}")
    with open(file_path, "wb") as f:
        content = file.file.read()
        if len(content) > MAX_FILE_SIZE:
            raise ValidationException(detail="File too large (max 20MB)")
        f.write(content)

    actual_size = os.path.getsize(file_path)
    task = service.create_task(
        db=db, hospital_id=hospital_id, user_id=current_user.user_id,
        file_path=file_path, filename=file.filename, file_type=file_type,
        file_size=actual_size,
    )
    return schemas.TaskStatusResponse(
        task_id=task.id, status=task.status, error_message=None,
        created_at=task.created_at, completed_at=None,
    )


@router.get("/tasks/{task_id}", response_model=schemas.TaskStatusResponse)
def get_task_status(task_id: int, db: Session = Depends(_get_db)):
    task = service.get_task_status(db, task_id)
    if not task:
        raise NotFoundException(detail="Task not found")
    return task


@router.get("/reports", response_model=schemas.ReportListResponse)
def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(_get_db),
    hospital_id: str = Depends(_get_hospital_id),
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = None if current_user.role != "user" else current_user.user_id
    items, total = service.list_reports(db, hospital_id, user_id, page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/reports/{report_id}", response_model=schemas.ReportDetailResponse)
def get_report_detail(report_id: int, db: Session = Depends(_get_db)):
    report = service.get_report_detail(db, report_id)
    if not report:
        raise NotFoundException(detail="Report not found")
    indicators = service.get_report_indicators(db, report_id)
    return {
        "id": report.id, "task_id": report.task_id,
        "name": report.name, "gender": report.gender, "age": report.age,
        "report_date": report.report_date, "check_type": report.check_type,
        "unit_name": report.unit_name,
        "indicators": [
            {"item_name": i.item_name, "item_name_standard": i.item_name_standard,
             "item_code": i.item_code, "result_value": i.result_value,
             "unit": i.unit, "ref_range_low": i.ref_range_low,
             "ref_range_high": i.ref_range_high, "category": i.category}
            for i in indicators
        ],
        "created_at": report.created_at,
    }


@router.delete("/reports/{report_id}")
def delete_report(report_id: int, db: Session = Depends(_get_db)):
    report = service.get_report_detail(db, report_id)
    if not report:
        raise NotFoundException(detail="Report not found")
    db.delete(report)
    db.commit()
    return {"status": "deleted"}
```

- [ ] **Step 2: 注册路由到 main.py**

```python
# 在 main.py 添加
from app.modules.report.router import router as report_router
app.include_router(report_router, prefix="/api/v1/reports", tags=["reports"])
```

- [ ] **Step 3: 验证路由**

```bash
uv run python -c "from app.main import app; routes = [r.path for r in app.routes if hasattr(r, 'path')]; print([r for r in routes if 'report' in r.lower()])"
```

Expected: `['/api/v1/reports/upload', '/api/v1/reports/tasks/{task_id}', ...]`

- [ ] **Step 4: Commit**

```bash
git add app/modules/report/router.py app/main.py
git commit -m "feat(report): add REST API routes (upload + query)"
```

---

### Task 8: 完整性验证 + 推送

- [ ] **Step 1: 全量导入验证**

```bash
uv run python -c "
from app.modules.report.models import ReportTask, ReportInfo, ReportIndicator
from app.modules.report.schemas import TaskStatusResponse, ReportDetailResponse
from app.modules.report.service import create_task, process_task, list_reports
from app.modules.report.worker import start_worker
from app.modules.report.router import router
from app.core.image_preprocess import preprocess, detect_blur
from app.core.vlm_client import vlm_client
from app.core.term_normalizer import normalize_item_name
print('All imports OK')
"
```

- [ ] **Step 2: 服务器启动验证**

```bash
timeout 3 uv run uvicorn app.main:app --port 8001 2>&1 || true
```

Expected: `Application startup complete.`

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "chore(report): verify module integrity"
```

- [ ] **Step 4: 推送 + 合并**

```bash
git push -u origin feat/report-parsing
git checkout infra-setup
git merge feat/report-parsing
git push origin infra-setup
```
