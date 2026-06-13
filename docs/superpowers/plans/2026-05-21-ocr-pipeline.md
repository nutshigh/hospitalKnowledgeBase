# OCR 报告解析引擎 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 PaddleOCR 替换 VLM 作为报告解析主力引擎，实现本地化体检报告表格结构化提取，VLM 路径完整保留可切换。

**Architecture:** 新建 `ocr_pipeline.py` 核心模块（PaddleOCR + 行聚类 + 列映射引擎），通过 `REPORT_PARSING_ENGINE=ocr|vlm` 配置切换，修复 report router 的 hospital context 依赖顺序 bug。

**Tech Stack:** PaddleOCR >=2.9, paddlepaddle, PyMuPDF, numpy, opencv-python, pytest

---

## File Map

```
backend/
├── app/
│   ├── config.py                           [修改] 新增 REPORT_PARSING_ENGINE
│   ├── core/
│   │   └── ocr_pipeline.py                 [新建] OCR 主 pipeline + 列映射引擎
│   └── modules/report/
│       ├── router.py                       [修改] 修复 hospital context 依赖顺序
│       └── service.py                      [修改] process_task 按配置选择 OCR/VLM
├── .env.example                            [修改] 新增配置项
├── pyproject.toml                          [修改] 新增 paddleocr 依赖
└── tests/
    └── test_ocr_pipeline.py                [新建] 列映射 + ref_range 单元测试
```

---

### Task 1: 新增 PaddleOCR 依赖和配置

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/config.py:33-34`
- Modify: `backend/.env.example:33`

- [ ] **Step 1: 添加 paddleocr 依赖到 pyproject.toml**

在 `backend/pyproject.toml` 的 `dependencies` 列表中，`"pymupdf>=1.27.2.3",` 之后新增：

```toml
    "paddleocr>=2.9.0",
    "paddlepaddle",
```

- [ ] **Step 2: 添加配置到 config.py**

在 `backend/app/config.py` 的 `LLM_PROVIDER: str = "local"` 行之后追加：

```python
    # Report Parsing Engine: ocr | vlm
    REPORT_PARSING_ENGINE: str = "ocr"
```

- [ ] **Step 3: 添加配置到 .env.example**

在 `backend/.env.example` 的 `LLM_PROVIDER=local` 行之后追加：

```bash
# 报告解析引擎: ocr | vlm
REPORT_PARSING_ENGINE=ocr
```

- [ ] **Step 4: 安装依赖验证配置加载**

Run: `cd backend && uv sync`
Expected: 下载 paddlepaddle + paddleocr，无错误

Run: `cd backend && uv run python -c "from app.config import settings; print(settings.REPORT_PARSING_ENGINE)"`
Expected: 输出 `ocr`

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/app/config.py backend/.env.example backend/uv.lock
git commit -m "feat: add paddleocr dependency and REPORT_PARSING_ENGINE config"
```

---

### Task 2: 创建 OCR Pipeline — 基础框架

**Files:**
- Create: `backend/app/core/ocr_pipeline.py`
- Test: `backend/tests/test_ocr_pipeline.py`

- [ ] **Step 1: 写 ref_range 拆分的测试**

```python
# backend/tests/test_ocr_pipeline.py
import pytest
from app.core.ocr_pipeline import OcrPipeline


@pytest.fixture
def pipeline():
    return OcrPipeline()


class TestParseRefRange:
    def test_standard_range(self, pipeline):
        assert pipeline._parse_ref_range("3.5-9.5") == ("3.5", "9.5")

    def test_range_with_spaces(self, pipeline):
        assert pipeline._parse_ref_range("3.5 - 9.5") == ("3.5", "9.5")

    def test_range_with_tilde(self, pipeline):
        assert pipeline._parse_ref_range("3.5~9.5") == ("3.5", "9.5")

    def test_chinese_separator(self, pipeline):
        assert pipeline._parse_ref_range("3.5到9.5") == ("3.5", "9.5")
        assert pipeline._parse_ref_range("3.5至9.5") == ("3.5", "9.5")

    def test_less_than(self, pipeline):
        assert pipeline._parse_ref_range("<5.0") == (None, "5.0")
        assert pipeline._parse_ref_range("＜5.0") == (None, "5.0")

    def test_greater_than(self, pipeline):
        assert pipeline._parse_ref_range(">100") == ("100", None)
        assert pipeline._parse_ref_range("＞100") == ("100", None)

    def test_non_range_text(self, pipeline):
        assert pipeline._parse_ref_range("正常") == (None, None)
        assert pipeline._parse_ref_range("阴性") == (None, None)

    def test_decimal_range(self, pipeline):
        assert pipeline._parse_ref_range("0.00-0.50") == ("0.00", "0.50")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_ocr_pipeline.py::TestParseRefRange -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.ocr_pipeline'`

- [ ] **Step 3: 创建 ocr_pipeline.py 最小骨架（含 _parse_ref_range）**

```python
# backend/app/core/ocr_pipeline.py
import re
import numpy as np
from typing import Optional
from dataclasses import dataclass


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
        self._ocr = None

    def _init(self):
        if self._initialized:
            return
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(
            use_angle_cls=True, lang="ch",
            use_gpu=self._use_gpu, show_log=False,
        )
        self._initialized = True

    # ── 未来 Task 实现的方法占位 ──

    def extract_from_pdf(self, file_path: str) -> dict:
        raise NotImplementedError

    def extract_from_image(self, file_path: str) -> dict:
        raise NotImplementedError

    def _pdf_to_images(self, file_path: str) -> list:
        raise NotImplementedError

    def _process_page(self, image, page_index: int) -> dict:
        raise NotImplementedError

    def _extract_personal_info(self, image) -> dict:
        raise NotImplementedError

    def _flatten_text(self, ocr_result) -> list:
        raise NotImplementedError

    def _extract_table_indicators(self, ocr_result, image) -> list:
        raise NotImplementedError

    def _group_text_lines(self, ocr_result) -> list:
        raise NotImplementedError

    def _find_header_row(self, rows: list) -> list:
        raise NotImplementedError

    def _match_header_columns(self, headers: list) -> dict:
        raise NotImplementedError

    def _row_to_indicator(self, row_cells: list, col_mapping: dict) -> dict:
        raise NotImplementedError

    # ── 已实现 ──

    def _parse_ref_range(self, text: str) -> tuple:
        text = text.strip()
        m = re.match(r"([\d.]+)\s*[-~—到至]\s*([\d.]+)", text)
        if m:
            return m.group(1), m.group(2)
        m = re.match(r"[<＜]\s*([\d.]+)", text)
        if m:
            return None, m.group(1)
        m = re.match(r"[>＞]\s*([\d.]+)", text)
        if m:
            return m.group(1), None
        return None, None

    def _looks_like_number(self, text: str) -> bool:
        return bool(re.match(r"^[\d.]+$", text))

    def _looks_like_ref_range(self, text: str) -> bool:
        return bool(re.match(r"^[<＞>\d].*[\d]|[～-].*[\d]", text))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_ocr_pipeline.py::TestParseRefRange -v`
Expected: 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ocr_pipeline.py backend/tests/test_ocr_pipeline.py
git commit -m "feat: add OcrPipeline skeleton with _parse_ref_range"
```

---

### Task 3: 列映射引擎 + 行聚类

**Files:**
- Modify: `backend/app/core/ocr_pipeline.py`
- Modify: `backend/tests/test_ocr_pipeline.py`

- [ ] **Step 1: 写列映射测试**

在 `backend/tests/test_ocr_pipeline.py` 的 `TestParseRefRange` 类之后追加：

```python
class TestMatchHeaderColumns:
    @pytest.fixture
    def pipeline(self):
        return OcrPipeline()

    def test_exact_match(self, pipeline):
        headers = [
            {"text": "检验项目", "bbox": [[0,0],[50,0],[50,20],[0,20]], "confidence": 0.99, "cx": 25, "cy": 10},
            {"text": "结果", "bbox": [[50,0],[80,0],[80,20],[50,20]], "confidence": 0.99, "cx": 65, "cy": 10},
            {"text": "单位", "bbox": [[80,0],[100,0],[100,20],[80,20]], "confidence": 0.99, "cx": 90, "cy": 10},
            {"text": "参考范围", "bbox": [[100,0],[150,0],[150,20],[100,20]], "confidence": 0.99, "cx": 125, "cy": 10},
        ]
        mapping = pipeline._match_header_columns(headers)
        assert mapping[0] == "item_name"
        assert mapping[1] == "result"
        assert mapping[2] == "unit"
        assert mapping[3] == "ref_range"

    def test_partial_match(self, pipeline):
        headers = [
            {"text": "项目名称", "bbox": [[0,0],[50,0],[50,20],[0,20]], "confidence": 0.99, "cx": 25, "cy": 10},
            {"text": "测定值", "bbox": [[50,0],[80,0],[80,20],[50,20]], "confidence": 0.99, "cx": 65, "cy": 10},
            {"text": "英文缩写", "bbox": [[80,0],[110,0],[110,20],[80,20]], "confidence": 0.99, "cx": 95, "cy": 10},
        ]
        mapping = pipeline._match_header_columns(headers)
        assert mapping[0] == "item_name"
        assert mapping[1] == "result"
        assert mapping[2] == "item_code"

    def test_unknown_column(self, pipeline):
        headers = [
            {"text": "检验项目", "bbox": [[0,0],[50,0],[50,20],[0,20]], "confidence": 0.99, "cx": 25, "cy": 10},
            {"text": "XYZ奇怪列", "bbox": [[50,0],[100,0],[100,20],[50,20]], "confidence": 0.99, "cx": 75, "cy": 10},
        ]
        mapping = pipeline._match_header_columns(headers)
        assert mapping[0] == "item_name"
        assert mapping[1] == "unknown"

    def test_empty_headers(self, pipeline):
        mapping = pipeline._match_header_columns([])
        assert mapping == {}


class TestRowToIndicator:
    @pytest.fixture
    def pipeline(self):
        return OcrPipeline()

    def test_basic_row(self, pipeline):
        row = [
            {"text": "白细胞", "confidence": 0.95, "cx": 25, "cy": 50},
            {"text": "5.2", "confidence": 0.98, "cx": 65, "cy": 50},
            {"text": "10^9/L", "confidence": 0.97, "cx": 90, "cy": 50},
            {"text": "3.5-9.5", "confidence": 0.96, "cx": 125, "cy": 50},
        ]
        col_mapping = {0: "item_name", 1: "result", 2: "unit", 3: "ref_range"}
        result = pipeline._row_to_indicator(row, col_mapping)
        assert result["item_name"] == "白细胞"
        assert result["result_value"] == "5.2"
        assert result["unit"] == "10^9/L"
        assert result["ref_range_low"] == "3.5"
        assert result["ref_range_high"] == "9.5"

    def test_unknown_column_inference(self, pipeline):
        row = [
            {"text": "总蛋白", "confidence": 0.95, "cx": 25, "cy": 50},
            {"text": "72.5", "confidence": 0.98, "cx": 65, "cy": 50},
            {"text": "60.0-80.0", "confidence": 0.96, "cx": 90, "cy": 50},
        ]
        col_mapping = {0: "item_name", 1: "unknown", 2: "unknown"}
        result = pipeline._row_to_indicator(row, col_mapping)
        assert result["item_name"] == "总蛋白"
        assert result["result_value"] == "72.5"
        assert result["ref_range_low"] == "60.0"
        assert result["ref_range_high"] == "80.0"

    def test_skip_row_without_item_name(self, pipeline):
        row = [
            {"text": "", "confidence": 0.5, "cx": 25, "cy": 50},
        ]
        col_mapping = {0: "item_name"}
        result = pipeline._row_to_indicator(row, col_mapping)
        assert result == {}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_ocr_pipeline.py -v`
Expected: `TestMatchHeaderColumns` 和 `TestRowToIndicator` 的测试 FAIL (NotImplementedError)

- [ ] **Step 3: 实现 _match_header_columns, _row_to_indicator**

替换 `ocr_pipeline.py` 中三个 `raise NotImplementedError` 方法为实际实现：

```python
    def _match_header_columns(self, headers: list[dict]) -> dict:
        mapping = {}

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
        result = {}
        for i, cell in enumerate(row_cells):
            key = col_mapping.get(i, "unknown")
            text = cell["text"].strip()

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
                pass
            else:
                if self._looks_like_number(text) and "result_value" not in result:
                    result["result_value"] = text
                elif self._looks_like_ref_range(text) and "ref_range_low" not in result:
                    low, high = self._parse_ref_range(text)
                    result["ref_range_low"] = low
                    result["ref_range_high"] = high

        result["confidence"] = min(
            c["confidence"] for c in row_cells
        ) if row_cells else 1.0

        return result if result.get("item_name") else {}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && uv run pytest tests/test_ocr_pipeline.py -v`
Expected: 17 tests PASS (9 ref_range + 8 mapping/indicator)

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ocr_pipeline.py backend/tests/test_ocr_pipeline.py
git commit -m "feat: add column mapping engine and row-to-indicator logic"
```

---

### Task 4: 行聚类 + 表头查找

**Files:**
- Modify: `backend/app/core/ocr_pipeline.py`
- Modify: `backend/tests/test_ocr_pipeline.py`

- [ ] **Step 1: 写行聚类和表头查找测试**

在 `TestRowToIndicator` 类之后追加：

```python
class TestGroupTextLines:
    @pytest.fixture
    def pipeline(self):
        return OcrPipeline()

    def test_two_rows(self, pipeline):
        ocr_result = [
            [
                [[[0, 0], [50, 0], [50, 18], [0, 18]], ["项目", 0.99]],
                [[[60, 0], [90, 0], [90, 18], [60, 18]], ["结果", 0.99]],
                [[[0, 22], [50, 22], [50, 40], [0, 40]], ["白细胞", 0.98]],
                [[[60, 22], [90, 22], [90, 40], [60, 40]], ["5.2", 0.97]],
            ]
        ]
        rows = pipeline._group_text_lines(ocr_result)
        assert len(rows) == 2
        assert rows[0][0]["text"] == "项目"
        assert rows[1][0]["text"] == "白细胞"

    def test_empty_result(self, pipeline):
        rows = pipeline._group_text_lines(None)
        assert rows == []


class TestFindHeaderRow:
    @pytest.fixture
    def pipeline(self):
        return OcrPipeline()

    def test_find_header_by_keyword_density(self, pipeline):
        rows = [
            [{"text": "医院名称", "cx": 100, "cy": 10, "bbox": [[0,0],[80,0],[80,18],[0,18]], "confidence": 0.99}],
            [{"text": "检验项目", "cx": 25, "cy": 30, "bbox": [[0,20],[50,20],[50,38],[0,38]], "confidence": 0.99},
             {"text": "结果", "cx": 75, "cy": 30, "bbox": [[60,20],[90,20],[90,38],[60,38]], "confidence": 0.99},
             {"text": "参考范围", "cx": 130, "cy": 30, "bbox": [[100,20],[155,20],[155,38],[100,38]], "confidence": 0.99}],
        ]
        header = pipeline._find_header_row(rows)
        assert len(header) == 3
        assert header[0]["text"] == "检验项目"

    def test_no_keywords_returns_empty(self, pipeline):
        rows = [
            [{"text": "报告日期", "cx": 100, "cy": 10, "bbox": [[0,0],[80,0],[80,18],[0,18]], "confidence": 0.99}],
            [{"text": "白细胞", "cx": 25, "cy": 30, "bbox": [[0,20],[50,20],[50,38],[0,38]], "confidence": 0.99}],
        ]
        header = pipeline._find_header_row(rows)
        assert len(header) == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && uv run pytest tests/test_ocr_pipeline.py::TestGroupTextLines tests/test_ocr_pipeline.py::TestFindHeaderRow -v`
Expected: FAIL (NotImplementedError)

- [ ] **Step 3: 实现 _group_text_lines, _find_header_row**

替换 `ocr_pipeline.py` 中对应的 `raise NotImplementedError`：

```python
    def _group_text_lines(self, ocr_result) -> list[list[dict]]:
        if not ocr_result or not ocr_result[0]:
            return []

        items = []
        for line in ocr_result[0]:
            if len(line) >= 2:
                bbox = line[0]
                text = str(line[1][0])
                conf = float(line[1][1])
                items.append({
                    "text": text,
                    "bbox": bbox,
                    "confidence": conf,
                    "cx": (bbox[0][0] + bbox[2][0]) / 2,
                    "cy": (bbox[0][1] + bbox[2][1]) / 2,
                })

        items.sort(key=lambda x: x["cy"])
        if not items:
            return []

        avg_h = sum(abs(it["bbox"][2][1] - it["bbox"][0][1]) for it in items) / len(items)
        rows = []
        current_row = [items[0]]
        for it in items[1:]:
            if abs(it["cy"] - current_row[-1]["cy"]) < avg_h * 1.5:
                current_row.append(it)
            else:
                current_row.sort(key=lambda x: x["cx"])
                rows.append(current_row)
                current_row = [it]
        if current_row:
            current_row.sort(key=lambda x: x["cx"])
            rows.append(current_row)
        return rows

    def _find_header_row(self, rows: list) -> list[dict]:
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
```

- [ ] **Step 4: 跑全量测试确认通过**

Run: `cd backend && uv run pytest tests/test_ocr_pipeline.py -v`
Expected: 21 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ocr_pipeline.py backend/tests/test_ocr_pipeline.py
git commit -m "feat: add text line clustering and header row detection"
```

---

### Task 5: PDF 渲染 + 个人信息提取

**Files:**
- Modify: `backend/app/core/ocr_pipeline.py`

- [ ] **Step 1: 实现 _pdf_to_images, _flatten_text, _extract_personal_info**

替换 `ocr_pipeline.py` 中对应的 `raise NotImplementedError`：

```python
    def _pdf_to_images(self, file_path: str) -> list:
        import fitz
        doc = fitz.open(file_path)
        images = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 4:
                img = img[:, :, :3]
            images.append(img)
        doc.close()
        return images

    def _flatten_text(self, ocr_result) -> list[str]:
        texts = []
        if ocr_result and ocr_result[0]:
            for line in ocr_result[0]:
                if len(line) >= 2:
                    texts.append(str(line[1][0]))
        return texts

    def _extract_personal_info(self, image) -> dict:
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
        full = "\n".join(all_text)
        for key, pat in patterns.items():
            m = re.search(pat, full)
            if m:
                info[key] = m.group(1)
        return info
```

- [ ] **Step 2: 实现 _process_page, _extract_table_indicators**

替换 `ocr_pipeline.py` 中对应的 `raise NotImplementedError`：

```python
    def _process_page(self, image, page_index: int) -> dict:
        self._init()
        result = self._ocr.ocr(image, cls=True)

        personal_info = {}
        if page_index == 0:
            personal_info = self._extract_personal_info(image)

        indicators = self._extract_table_indicators(result, image)
        return {"personal_info": personal_info, "indicators": indicators}

    def _extract_table_indicators(self, ocr_result, image) -> list[dict]:
        lines = self._group_text_lines(ocr_result)
        if len(lines) < 2:
            return []

        header_idx = 0
        all_keywords = set()
        for aliases in COLUMN_KEYWORDS.values():
            all_keywords.update(aliases)

        best_score = 0
        for i, row in enumerate(lines[:min(5, len(lines))]):
            text = " ".join(cell["text"] for cell in row)
            score = sum(1 for kw in all_keywords if kw in text)
            if score > best_score:
                best_score = score
                header_idx = i

        if best_score == 0:
            return []

        headers = lines[header_idx]
        col_mapping = self._match_header_columns(headers)

        indicators = []
        for row in lines[header_idx + 1:]:
            indicator = self._row_to_indicator(row, col_mapping)
            if indicator and indicator.get("item_name"):
                indicators.append(indicator)
        return indicators
```

- [ ] **Step 3: 实现主入口 extract_from_pdf, extract_from_image**

替换 `ocr_pipeline.py` 中对应的 `raise NotImplementedError`：

```python
    def extract_from_pdf(self, file_path: str) -> dict:
        images = self._pdf_to_images(file_path)
        all_indicators = []
        personal_info = {}

        for i, img in enumerate(images):
            page_result = self._process_page(img, page_index=i)
            if i == 0 and page_result.get("personal_info"):
                personal_info = page_result["personal_info"]
            all_indicators.extend(page_result.get("indicators", []))

        return {"personal_info": personal_info, "indicators": all_indicators}

    def extract_from_image(self, file_path: str) -> dict:
        import cv2
        img = cv2.imread(file_path)
        return self._process_page(img, page_index=0)
```

- [ ] **Step 4: 验证导入无语法错误**

Run: `cd backend && uv run python -c "from app.core.ocr_pipeline import OcrPipeline; p = OcrPipeline(); print('Import OK')"`
Expected: `Import OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/ocr_pipeline.py
git commit -m "feat: implement PDF rendering, personal info extraction, and main entry points"
```

---

### Task 6: 修复 report router hospital context bug + 集成 OCR 到 process_task

**Files:**
- Modify: `backend/app/modules/report/router.py`
- Modify: `backend/app/modules/report/service.py`

- [ ] **Step 1: 修复 router.py 的 hospital context 依赖顺序**

`router.py` 的 `_get_hospital_id` 和 `_get_db` 改为直接从 `CurrentUser` 提取 hospital_id（与 chat router 相同的修复），同时移除 `get_current_hospital_id` 导入：

```python
# 修改 imports 部分
from app.core.dependencies import get_current_user, CurrentUser
# 删除: from app.middleware.hospital_context import get_current_hospital_id

# 替换 _get_hospital_id 和 _get_db:
def _get_db(current_user: CurrentUser = Depends(get_current_user)):
    if not current_user.hospital_id:
        raise ValidationException(detail="Hospital context required")
    return next(get_hospital_db(current_user.hospital_id))
```

修改所有使用 `_get_hospital_id` 的端点签名：移除 `hospital_id: str = Depends(_get_hospital_id)` 参数，改为从 `current_user.hospital_id` 获取。

upload 端点改为：
```python
@router.post("/upload")
def upload_report(
    file: UploadFile = File(...),
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    # ... 内部使用 current_user.hospital_id 替代原来的 hospital_id
```

list_reports 端点同理，移除 `hospital_id: str = Depends(_get_hospital_id)`，用 `current_user.hospital_id` 代替。

- [ ] **Step 2: 修改 service.py process_task 按配置选择 OCR/VLM**

在 `backend/app/modules/report/service.py` 的 `process_task()` 函数中，将现有的 VLM 调用包裹在 `else` 分支，新增 OCR 分支：

```python
def process_task(db: Session, task_id: int, hospital_id: str):
    task = get_task_status(db, task_id)
    if not task:
        return

    task.status = "parsing"
    db.commit()

    try:
        user_dir = os.path.dirname(task.original_file_path)

        if task.file_type == "image":
            processed_path, error_msg = preprocess(task.original_file_path, user_dir)
            if error_msg:
                task.status = "failed"
                task.error_message = error_msg
                db.commit()
                return
        else:
            processed_path = task.original_file_path

        if settings.REPORT_PARSING_ENGINE == "ocr":
            from app.core.ocr_pipeline import OcrPipeline
            ocr = OcrPipeline(use_gpu=False)
            if task.file_type == "pdf":
                result = ocr.extract_from_pdf(processed_path)
            else:
                result = ocr.extract_from_image(processed_path)
        else:
            images_b64 = _file_to_base64_list(processed_path, task.file_type)
            result = vlm_client.extract_from_images(images_b64)

        indicators = normalize_indicators(result.get("indicators", []))
        personal_info = result.get("personal_info", {})

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
        task.completed_at = datetime.utcnow()
        db.commit()

        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=hospital_id, priority=task.priority,
            payload={"report_id": report.id, "hospital_id": hospital_id},
        ))

    except Exception as e:
        task.retry_count += 1
        task.status = "failed" if task.retry_count >= 3 else "queued"
        task.error_message = str(e)
        db.commit()
```

注意：`settings` 需要导入 — 检查文件顶部 imports，如未导入则添加 `from app.config import settings`。

- [ ] **Step 3: 验证导入无错误**

Run: `cd backend && uv run python -c "from app.modules.report.router import router; from app.modules.report.service import process_task; print('Import OK')"`
Expected: `Import OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/modules/report/router.py backend/app/modules/report/service.py
git commit -m "feat: integrate OCR pipeline into process_task with config toggle, fix hospital context bug"
```

---

### Task 7: 集成验证

**Files:** (无新建，验证现有代码)

- [ ] **Step 1: 验证 OCR pipeline 可导入且列映射逻辑正确**

Run: `cd backend && uv run pytest tests/test_ocr_pipeline.py -v`
Expected: 21 tests PASS

- [ ] **Step 2: 验证后端启动无 import 错误**

Run: `cd backend && timeout 5 uv run uvicorn app.main:app --port 8001 2>&1 || true`
Expected: 无 ImportError，正常启动日志

- [ ] **Step 3: 验证配置文件加载**

Run: `cd backend && uv run python -c "from app.config import settings; print(settings.REPORT_PARSING_ENGINE); print(settings.LLM_PROVIDER)"`
Expected:
```
ocr
local
```

- [ ] **Step 4: Commit 最终状态（如有改动）**

```bash
git status
git add -A
git commit -m "chore: final integration verification for OCR pipeline"  # (仅如有改动)
```
