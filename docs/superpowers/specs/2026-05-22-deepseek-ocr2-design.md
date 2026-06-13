# DeepSeek-OCR-2 vLLM 本地部署适配 — 设计文档

**Goal:** 将 `vlm_client.py` 适配为 DeepSeek-OCR-2 的正确调用方式：vLLM serve HTTP 服务 + Markdown 表格输出 + 后解析提取结构化数据。

**Key decisions:**
- vLLM serve HTTP 方案（兼容 OpenAI `/chat/completions`），与现有 vLLM 基础设施统一
- Prompt 要求 Markdown 表格输出，替代当前 JSON 格式
- 对外接口 (`extract_from_image` / `extract_from_images`) 签名不变，service.py 不改

---

## 部署架构

```
┌──────────────┐     base64 image + prompt      ┌─────────────────┐
│  FastAPI     │ ─────────────────────────────── │  vLLM serve      │
│  vlm_client  │    HTTP POST /chat/completions  │  port 8001       │
│  (重写)      │ <───────────────────────────── │  DeepSeek-OCR-2  │
└──────────────┘     Markdown text response       └─────────────────┘
```

### vLLM serve 启动命令

```bash
git clone https://github.com/deepseek-ai/DeepSeek-OCR-2.git
cd DeepSeek-OCR-2

pip install torch==2.6.0 transformers==4.46.3 flash-attn==2.7.3 --no-build-isolation
pip install vllm>=0.8.5

vllm serve deepseek-ai/DeepSeek-OCR-2 \
  --port 8001 \
  --trust-remote-code \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.9 \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0
```

**硬件要求：** 1× GPU with ≥ 8GB VRAM（3B 模型，bf16 约 6GB）

---

## 数据流

```
PDF/图片上传
    │
    ▼
image_preprocess (现有，不动)
    │
    ▼
_file_to_base64_list (现有，不动)
  PDF → PyMuPDF 渲染 → base64 list
  单图 → base64 list[1]
    │
    ▼
┌───────────────────────────────────────────┐
│ vlm_client (重写)                          │
│                                            │
│ 1. 构建 prompt（Markdown 表格格式要求）     │
│ 2. POST /chat/completions（标准 API）      │
│ 3. 接收 Markdown 文本                       │
│ 4. 解析 Markdown 表格 → indicators[]        │
│ 5. 解析个人信息行 → personal_info           │
│ 6. 多图/多页合并                            │
└───────────────────────────────────────────┘
    │
    ▼
term_normalizer (现有，不动)
    │
    ▼
ReportInfo + ReportIndicator → DB
```

---

## 文件改动清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `app/core/vlm_client.py` | **重写** | 新 prompt + Markdown 解析 + 标准 API 调用 |
| `app/config.py` | 小改 | 新增 `OCR_PROMPT` 可自定义配置 |
| `.env.example` | 小改 | 更新注释，与代码一致 |
| `.env` | 不改 | 现有配置已正确 |
| `app/modules/report/service.py` | **不改** | 接口签名不变 |

---

## 核心设计

### 1. Prompt 设计

```
<image>\n<|grounding|>Extract all lab test indicators from this medical report
and output as a Markdown table.

## Personal Info
**Name:** <patient name>
**Gender:** <male/female>
**Age:** <number>
**Date:** <exam date YYYY-MM-DD>

## Indicators Table
| 项目名称 | 结果 | 单位 | 参考范围低 | 参考范围高 |
| --- | --- | --- | --- | --- |
| ... | ... | ... | ... | ... |

Rules:
1. Reference range "3.5-9.5" → ref_low="3.5", ref_high="9.5"
2. "<5.0" → ref_high="5.0", ref_low=null
3. Null fields: leave cell empty
4. Output EXACTLY one indicators table
```

### 2. Markdown 解析器 (`_parse_markdown_response`)

```
输入: Markdown 文本
  ├── 正则匹配 Personal Info 段 → personal_info dict
  └── 正则匹配 |...|...| 表格行 → 按列映射 → indicators list
       └── 空单元格 → None
       └── 数字字符串 → 保持原样
```

列映射逻辑（按表头关键词匹配）：
- 项目名称 / 检验项目 → `item_name`
- 结果 / 测定值 → `result`
- 单位 → `unit`
- 参考范围低 → `ref_low`
- 参考范围高 → `ref_high`

### 3. 错误处理

| 场景 | 处理 |
|------|------|
| vLLM 服务不可用 | httpx 抛异常，worker 捕获 → retry |
| 响应不是有效 Markdown | 尝试宽松匹配，仍失败则整体作为 raw_text |
| 表格无数据行 | 返回空 indicators，不报错 |
| 单页无个人信息 | personal_info 为空 dict，由多页合并填充 |

### 4. 对外接口（不变）

```python
class VLMClient:
    def extract_from_image(self, image_base64: str) -> dict:
        # POST /chat/completions → Markdown → parse → dict
        ...

    def extract_from_images(self, images_base64: list[str]) -> dict:
        # 逐张调用，合并 personal_info + indicators
        ...
```

---

## 依赖

无需新增依赖。现有 `httpx`、`PyMuPDF` 已满足。

如需本地 GPU 运行 vLLM serve，需额外安装：
```bash
pip install torch==2.6.0 flash-attn==2.7.3 vllm>=0.8.5
```

---

## 自检

- [x] vLLM serve 标准 API，去掉 vllm_xargs
- [x] Prompt 改为 Markdown 表格输出
- [x] Markdown 表格解析覆盖双边/单边参考范围
- [x] 多页 PDF 逐页处理 + 合并
- [x] service.py 接口不变，零改动
- [x] 个人信息 + 指标表格在同一个 prompt 中一次提取
- [x] 硬件要求明确（1x GPU ≥ 8GB VRAM）
