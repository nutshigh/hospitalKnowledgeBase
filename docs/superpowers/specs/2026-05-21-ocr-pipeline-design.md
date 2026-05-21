# OCR 报告解析引擎 — 设计文档

**Goal:** 将报告解析从 VLM 为主改为 OCR 为主力，使用 PaddleOCR + PP-Structure 实现本地化的体检报告表格结构化提取。VLM 完整保留作为未来优化项。

**Architecture:** 新增 `ocr_pipeline` 核心模块，封装 PaddleOCR 文字检测/识别 + PP-Structure 版面分析 + SLANet 表格结构识别 + 列映射引擎。通过 `.env` 配置 `REPORT_PARSING_ENGINE=ocr|vlm` 切换，保留现有 VLM 路径。

**Tech Stack:** PaddleOCR (PP-OCRv4, PP-Structure, SLANet), PyMuPDF, opencv-python, NumPy

---

## 数据流

```
PDF/图片上传
    │
    ▼
image_preprocess (现有，不动)
  模糊检测 → 寻边裁剪 → 倾斜校正
    │
    ▼
┌───────────────────────────────────────────┐
│ ocr_pipeline (新增)                        │
│                                            │
│ 1. PyMuPDF 渲染 PDF → 图片列表             │
│ 2. PP-Structure 版面分析 → 表格区域定位     │
│ 3. SLANet → 表格行列结构还原 (cell级)       │
│ 4. PP-OCRv4 → 逐 cell 文字识别             │
│ 5. 列映射引擎 → 匹配指标/简称/结果/单位/参考 │
│ 6. 多页合并 → indicators[] + personal_info │
└───────────────────────────────────────────┘
    │
    ▼
term_normalizer (现有，不动)
  医学术语标准化
    │
    ▼
ReportInfo + ReportIndicator → DB
```

---

## 模型选型

| 组件 | 模型 | 用途 | 大小 |
|------|------|------|------|
| 文字检测 | PP-OCRv4 det | 定位图中所有文字区域 | ~12MB |
| 文字识别 | PP-OCRv4 rec | 识别区域中文+英文+数字 | ~14MB |
| 版面分析 | PP-Structure layout | 区分表格/文字/图片区域 | ~20MB |
| 表格结构 | SLANet | 还原行列归属 (row/col/rowspan/colspan) | ~25MB |

总计约 71MB，首次运行自动下载到 `~/.paddleocr/`。

---

## 文件结构

```
backend/app/core/
├── ocr_pipeline.py       [新建] OCR 主 pipeline + 列映射引擎
├── vlm_client.py          [不动] VLM 路径完整保留
├── image_preprocess.py    [不动]
├── doc_parser.py          [不动]

backend/app/modules/report/
├── service.py             [修改] process_task 按配置选择 OCR/VLM 路径
├── router.py              [修改] 修复 hospital context 依赖顺序

backend/app/config.py      [修改] 新增 REPORT_PARSING_ENGINE 配置
backend/.env.example       [修改] 新增配置项
```

---

## 核心模块设计

### 1. OCR Pipeline (`backend/app/core/ocr_pipeline.py`)

```python
import fitz
import numpy as np
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict
import math


@dataclass
class Cell:
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    text: str = ""
    confidence: float = 1.0
    bbox: Optional[list] = None


@dataclass
class Indicator:
    item_name: str
    item_name_standard: Optional[str] = None
    item_code: Optional[str] = None
    result_value: Optional[str] = None
    unit: Optional[str] = None
    ref_range_low: Optional[str] = None
    ref_range_high: Optional[str] = None
    raw_text: Optional[str] = None
    confidence: float = 1.0


COLUMN_KEYWORDS = {
    "item_name": ["项目", "检验项目", "指标名称", "检查项目", "测定项目", "项目名称", "检查指标"],
    "item_code": ["缩写", "英文简称", "代码", "英文缩写", "代号", "缩写符号"],
    "result":    ["结果", "测定值", "检验结果", "实测值", "检测结果", "测定结果", "检查结果", "数值"],
    "unit":      ["单位", "计量单位", "测量单位"],
    "ref_range": ["参考区间", "参考范围", "正常范围", "参考值", "正常值", "参考", "标准范围"],
    "flag":      ["提示", "标志", "异常", "箭头", "↑↓", "判断"],
}


class OcrPipeline:
    def __init__(self, use_gpu: bool = False):
        self._use_gpu = use_gpu
        self._initialized = False
        self._ocr = None       # PaddleOCR 实例
        self._table_engine = None  # PP-Structure 表格引擎

    def _init(self):
        """懒加载，首次调用时初始化 PaddleOCR 模型"""
        if self._initialized:
            return
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(
            use_angle_cls=True,
            lang="ch",
            use_gpu=self._use_gpu,
            show_log=False,
        )
        self._initialized = True

    # ── 主入口 ──

    def extract_from_pdf(self, file_path: str) -> dict:
        """PDF → images → OCR → indicators"""
        images = self._pdf_to_images(file_path)
        all_indicators = []
        personal_info = {}

        for i, img in enumerate(images):
            page_result = self._process_page(img, page_index=i)
            if i == 0 and page_result.get("personal_info"):
                personal_info = page_result["personal_info"]
            all_indicators.extend(page_result.get("indicators", []))

        return {
            "personal_info": personal_info,
            "indicators": all_indicators,
        }

    def extract_from_image(self, file_path: str) -> dict:
        """单图 → OCR → indicators"""
        import cv2
        img = cv2.imread(file_path)
        return self._process_page(img, page_index=0)

    # ── PDF 渲染 ──

    def _pdf_to_images(self, file_path: str) -> list:
        """PyMuPDF 逐页渲染为 numpy array"""
        doc = fitz.open(file_path)
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 4:
                img = img[:, :, :3]  # RGBA → RGB
            images.append(img)
        doc.close()
        return images

    # ── 页面处理 ──

    def _process_page(self, image, page_index: int) -> dict:
        """处理单页图片，返回 indicators + personal_info"""
        self._init()

        # PP-Structure 表格识别
        result = self._ocr.ocr(image, cls=True)

        # 提取 personal_info (首页头部)
        personal_info = {}
        if page_index == 0:
            personal_info = self._extract_personal_info(image)

        # 提取表格指标
        indicators = self._extract_table_indicators(result, image)

        return {
            "personal_info": personal_info,
            "indicators": indicators,
        }

    # ── 个人信息提取 ──

    def _extract_personal_info(self, image) -> dict:
        """从 OCR 结果中提取姓名、性别、年龄、日期"""
        self._init()
        result = self._ocr.ocr(image, cls=True)
        all_text = self._flatten_text(result)

        info = {}
        patterns = {
            "name": r"姓名[:：\s]*(\S+)",
            "gender": r"性别[:：\s]*(男|女)",
            "age": r"年龄[:：\s]*(\d+)",
            "check_date": r"(?:检查|体检|报告)?日期[:：\s]*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2})",
        }
        import re
        full = "\n".join(all_text)
        for key, pat in patterns.items():
            m = re.search(pat, full)
            if m:
                info[key] = m.group(1)
        return info

    def _flatten_text(self, ocr_result) -> list[str]:
        """OCR 结果展平为文字列表"""
        texts = []
        if ocr_result and ocr_result[0]:
            for line in ocr_result[0]:
                if len(line) >= 2:
                    texts.append(str(line[1][0]))
        return texts

    # ── 表格指标提取 ──

    def _extract_table_indicators(self, ocr_result, image) -> list[dict]:
        """从 OCR 结果中提取表格指标"""
        # 1. 按 Y 坐标排序，聚类行
        # 2. 按 X 坐标排序每行内文字，聚类列
        # 3. 匹配表头 → 列映射
        # 4. 提取数据行

        lines = self._group_text_lines(ocr_result)
        if len(lines) < 2:
            return []

        # 表头匹配
        headers = self._find_header_row(lines)
        col_mapping = self._match_header_columns(headers)

        # 数据行提取
        indicators = []
        for row in lines[len(headers):]:
            indicator = self._row_to_indicator(row, col_mapping)
            if indicator and indicator.get("item_name"):
                indicators.append(indicator)

        return indicators

    def _group_text_lines(self, ocr_result) -> list[list[dict]]:
        """将 OCR 结果按 Y 坐标聚类成行"""
        if not ocr_result or not ocr_result[0]:
            return []

        items = []
        for line in ocr_result[0]:
            if len(line) >= 2:
                bbox = line[0]  # [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
                text = str(line[1][0])
                conf = float(line[1][1])
                items.append({
                    "text": text,
                    "bbox": bbox,
                    "confidence": conf,
                    "cx": (bbox[0][0] + bbox[2][0]) / 2,
                    "cy": (bbox[0][1] + bbox[2][1]) / 2,
                })

        # 按 Y 排序
        items.sort(key=lambda x: x["cy"])

        # 聚类成行 (Y 间距 < 平均字符高度)
        if not items:
            return []
        avg_h = sum(abs(it["bbox"][2][1] - it["bbox"][0][1]) for it in items) / len(items)
        rows = []
        current_row = [items[0]]
        for it in items[1:]:
            if abs(it["cy"] - current_row[-1]["cy"]) < avg_h * 1.5:
                current_row.append(it)
            else:
                # 行内按 X 排序
                current_row.sort(key=lambda x: x["cx"])
                rows.append(current_row)
                current_row = [it]
        if current_row:
            current_row.sort(key=lambda x: x["cx"])
            rows.append(current_row)
        return rows

    def _find_header_row(self, rows: list) -> list[dict]:
        """找表头行 (前5行中关键词密度最高的行)"""
        best_row, best_score = [], 0
        all_keywords = set()
        for aliases in COLUMN_KEYWORDS.values():
            all_keywords.update(aliases)

        for row in rows[:min(5, len(rows))]:
            text = " ".join(cell["text"] for cell in row)
            score = 0
            for kw in all_keywords:
                if kw in text:
                    score += 1
            if score > best_score:
                best_score = score
                best_row = row
        return best_row

    def _match_header_columns(self, headers: list[dict]) -> dict:
        """匹配表头列 → 标准字段映射"""
        mapping = {}  # col_index → standard_key

        def _jaccard(a: str, b: str) -> float:
            sa, sb = set(a), set(b)
            if not sa or not sb:
                return 0.0
            return len(sa & sb) / len(sa | sb)

        for i, cell in enumerate(headers):
            text = cell["text"].strip()
            best_key, best_score = None, 0.0
            for key, aliases in COLUMN_KEYWORDS.items():
                for alias in aliases:
                    score = _jaccard(text, alias)
                    if score > best_score:
                        best_score = score
                        best_key = key
            if best_score > 0.35:
                mapping[i] = best_key
            else:
                mapping[i] = "unknown"
        return mapping

    def _row_to_indicator(self, row_cells: list[dict], col_mapping: dict) -> dict:
        """将一行 cell 按列映射转成 indicator dict"""
        result = {}
        for i, cell in enumerate(row_cells):
            key = col_mapping.get(i, "unknown")
            text = cell["text"].strip()
            conf = cell["confidence"]

            if key == "item_name":
                result["item_name"] = text
            elif key == "item_code":
                result["item_code"] = text
            elif key == "result":
                result["result_value"] = text
            elif key == "unit":
                result["unit"] = text
            elif key == "ref_range":
                low, high = self._parse_ref_range(text)
                result["ref_range_low"] = low
                result["ref_range_high"] = high
            elif key == "flag":
                pass  # 忽略提示列 (↑↓ 等)
            else:
                # unknown 列: 数字推断为 result, 含 "-" 推断为 ref_range
                if self._looks_like_number(text):
                    if "result_value" not in result:
                        result["result_value"] = text
                elif self._looks_like_ref_range(text):
                    if "ref_range_low" not in result:
                        low, high = self._parse_ref_range(text)
                        result["ref_range_low"] = low
                        result["ref_range_high"] = high

        result["confidence"] = min(
            c["confidence"] for c in row_cells
        ) if row_cells else 1.0

        return result if result.get("item_name") else {}

    def _parse_ref_range(self, text: str) -> tuple:
        """拆分参考范围 "3.5-9.5" → ("3.5", "9.5")"""
        import re
        text = text.strip()
        m = re.match(r"([\d.]+)\s*[-~—到至]\s*([\d.]+)", text)
        if m:
            return m.group(1), m.group(2)
        # 单边范围 "<5.0" → low=None, high=5.0
        m = re.match(r"[<＜]\s*([\d.]+)", text)
        if m:
            return None, m.group(1)
        m = re.match(r"[>＞]\s*([\d.]+)", text)
        if m:
            return m.group(1), None
        return None, None

    def _looks_like_number(self, text: str) -> bool:
        import re
        return bool(re.match(r"^[\d.]+$", text))

    def _looks_like_ref_range(self, text: str) -> bool:
        import re
        return bool(re.match(r"^[<＞>\d].*[\d]|[～-].*[\d]", text))
```

---

### 2. 配置 (`backend/app/config.py`)

```python
# 在 LLM_PROVIDER 附近新增
REPORT_PARSING_ENGINE: str = "ocr"  # ocr | vlm
```

### 3. `.env.example` 新增

```bash
# 报告解析引擎: ocr | vlm
REPORT_PARSING_ENGINE=ocr
```

### 4. 修改 `service.py process_task()`

```python
if settings.REPORT_PARSING_ENGINE == "ocr":
    from app.core.ocr_pipeline import OcrPipeline
    ocr = OcrPipeline(use_gpu=False)
    result = ocr.extract_from_pdf(
        task.original_file_path if task.file_type == "pdf"
        else task.original_file_path
    )
    indicators = normalize_indicators(result.get("indicators", []))
    personal_info = result.get("personal_info", {})
else:
    # 现有 VLM 路径，代码不动
    images_b64 = _file_to_base64_list(processed_path, task.file_type)
    result = vlm_client.extract_from_images(images_b64)
    indicators = normalize_indicators(result.get("indicators", []))
    personal_info = result.get("personal_info", {})
```

### 5. 修复 `router.py` hospital context 依赖顺序 (与 chat 相同的 bug)

```python
# router.py _get_hospital_id / _get_db 改为:
def _get_db(current_user: CurrentUser = Depends(get_current_user)):
    if not current_user.hospital_id:
        raise ValidationException(detail="Hospital context required")
    return next(get_hospital_db(current_user.hospital_id))
```

---

## 置信度策略

| 置信度范围 | 处理方式 |
|-----------|---------|
| cell ≥ 0.9 | 直接使用 |
| cell 0.7~0.9 | 使用，标记 `low_confidence=True` |
| cell < 0.7 | 使用，标记 `needs_review=True` |
| 整行平均 < 0.5 | 跳过该行 |
| 整体 < 0.5 | task.status = `pending_review` |

---

## 依赖

```bash
# pyproject.toml 新增
paddlepaddle       # CPU 版，GPU 版用 paddlepaddle-gpu
paddleocr>=2.9.0
```

---

## 测试策略

1. **单元测试 — 列映射引擎**：构造各种表头组合，验证列映射正确性
2. **单元测试 — ref_range 拆分**：各种范围格式 ("3.5-9.5", "<2.0", ">100")
3. **集成测试 — 图片 OCR**：准备 3 份不同医院格式的体检报告截图，验证端到端提取
4. **集成测试 — PDF OCR**：多页 PDF 渲染+识别+合并

---

## 自检清单

- [x] 列映射引擎覆盖 6 种标准列类型 + unknown 推断
- [x] ref_range 支持双边/单边范围格式
- [x] 多页 PDF 逐页处理 + 合并
- [x] VLM 路径完整保留，配置切换
- [x] 模型懒加载，首次运行自动下载
- [x] 置信度分级 + 降级策略
- [ ] PP-Structure 表格结构识别 API 需在实现时根据 paddleocr 实际版本调整
