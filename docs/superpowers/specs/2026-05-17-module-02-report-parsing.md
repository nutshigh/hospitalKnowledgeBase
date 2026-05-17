# 模块02：报告解析模块 — 详细设计

## 在整体架构中的位置

```
接入层 → API网关 → 业务模块层
                      ├── 知识库模块
                      ├── 报告解析模块 ← 本文档
                      ├── AI解读模块
                      ├── 统计分析模块
                      └── 调度管理模块
                              ↓
                        基础设施层 → 数据层
```

报告解析模块是整个数据处理链路的起点，负责将用户上传的各类体检报告文件转化为结构化的 JSON 数据，供后续 AI 解读模块和统计分析模块消费。

---

## 1. 功能清单

| 编号 | 功能 | 说明 |
|------|------|------|
| P1 | 多端文件上传 | 提供移动端（拍照/相册）和 Web 端（拖拽/选择）上传入口 |
| P2 | 图像预处理 | 模糊检测、自动寻边裁剪、倾斜校正、图片压缩 |
| P3 | 多格式文件解析 | 支持 PDF（多页）、Word、JPG/PNG 图片格式 |
| P4 | OCR/VLM 信息提取 | 提取个人信息、检验项目名称、结果值、单位、参考区间 |
| P5 | 术语标准化 | 非标描述映射至标准医学名词及疾病编码 |
| P6 | 结构化输出 | 输出统一的结构化报告 JSON 格式 |
| P7 | 异步任务状态机 | 排队中 → 解析中 → 解析完成 / 解析失败 |
| P8 | 解析结果存储 | 结构化数据写入 MySQL，原始文件保存至文件存储 |

---

## 2. 模块依赖关系

```
报告解析模块
  │
  ├── 上游调用者: 用户端（上传报告）
  │
  ├── 下游消费者: AI解读模块（消费解析完成的报告）
  │
  ├── 依赖基础设施层:
  │     ├── 文件存储（原始文件及缩略图）
  │     ├── 消息队列 RabbitMQ（异步任务入队 + 完成事件发布）
  │     └── LLM 网关（调用本地部署的 VLM 模型）
  │
  └── 依赖数据层:
        └── MySQL（任务记录、解析结果）
```

| 方向 | 模块/组件 | 依赖内容 |
|------|-----------|----------|
| 被调用 | 用户端 | 上传报告文件 |
| 发布事件 | 消息队列 | 解析完成后发布事件，供 AI 解读模块消费 |
| 调用 | 文件存储 | 存储原始文件 + 缩略图 |
| 调用 | RabbitMQ | 任务入队、状态更新 |
| 调用 | LLM 网关（VLM） | OCR/视觉识别提取报告内容 |
| 调用 | MySQL | 任务表、报告表、指标表 |

---

## 3. 技术栈

| 类别 | 选型 | 用途 |
|------|------|------|
| 后端框架 | FastAPI | 提供上传和查询 API |
| 数据库 | MySQL | 任务记录、报告元数据、结构化指标 |
| 消息队列 | RabbitMQ | 异步任务排队、完成事件发布 |
| 图像预处理 | Pillow + OpenCV | 模糊检测、寻边裁剪、倾斜校正 |
| PDF 解析 | PyMuPDF | 多页 PDF 文本/图片提取 |
| Word 解析 | python-docx | Word 文档解析 |
| VLM 模型 | 本地部署视觉大模型（如 Qwen-VL） | 图片/PDF 页面的 OCR + 结构化信息提取 |
| 文件存储 | 本地文件系统或对象存储 | 原始文件归档 |

---

## 4. 数据库设计

### report_task — 报告任务表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| hospital_id | VARCHAR(32) | 所属医院 |
| user_id | BIGINT | 上传用户 |
| original_file_path | VARCHAR(500) | 原始文件存储路径 |
| original_filename | VARCHAR(200) | 原始文件名 |
| file_type | VARCHAR(10) | pdf / word / image |
| file_size | BIGINT | 文件大小（Byte） |
| thumbnail_path | VARCHAR(500) | 缩略图路径 |
| status | VARCHAR(20) | queued / parsing / completed / failed |
| priority | TINYINT | 优先级 0=普通 1=紧急 |
| retry_count | INT | 重试次数 |
| error_message | TEXT | 失败原因 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |
| completed_at | DATETIME | 完成时间 |

### report_info — 报告基本信息表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| task_id | BIGINT FK | 关联 report_task.id |
| hospital_id | VARCHAR(32) | 所属医院 |
| user_id | BIGINT | 所属用户 |
| name | VARCHAR(50) | 体检者姓名 |
| gender | VARCHAR(5) | 性别 |
| age | INT | 年龄 |
| report_date | DATE | 报告日期 |
| check_type | VARCHAR(20) | 个检 / 团检 |
| unit_name | VARCHAR(100) | 体检单位（团检时） |
| created_at | DATETIME | 创建时间 |

### report_indicator — 报告指标明细表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| report_id | BIGINT FK | 关联 report_info.id |
| item_name | VARCHAR(100) | 检验项目名称（原始） |
| item_name_standard | VARCHAR(100) | 标准化后的项目名称 |
| item_code | VARCHAR(50) | 标准疾病/检验编码 |
| result_value | VARCHAR(50) | 结果值 |
| unit | VARCHAR(20) | 单位 |
| ref_range_low | VARCHAR(50) | 参考区间下限 |
| ref_range_high | VARCHAR(50) | 参考区间上限 |
| category | VARCHAR(50) | 检验分类（血液/尿液/影像等） |
| raw_text | TEXT | 原始 OCR 文本片段 |

---

## 5. 处理流程

### 5.1 文件上传与预处理

```
1. 用户端上传文件
         ↓
2. 前端校验:
   - 文件格式（PDF/Word/JPG/PNG）
   - 文件大小（≤ 20MB）
   - 页数限制（PDF ≤ 50 页）
         ↓
3. 后端接收文件:
   - 生成文件 ID，保存至 {hospital_id}/reports/{YYYY}/{user_id}/
   - 写入 report_task 记录，status = queued
         ↓
4. 图像类文件预处理:
   - 模糊检测 → 不通过则返回提示"照片模糊，请重新拍摄"
   - 自动寻边裁剪 → 裁除背景边框
   - 倾斜校正 → 矫正倾斜角度
   - 缩略图生成 → 用于列表展示
         ↓
5. 预处理完成后，任务入 RabbitMQ 队列
   返回用户: task_id + status = queued
```

### 5.2 OCR/VLM 解析流程（Worker 消费）

```
1. Worker 从队列拉取任务，更新 status = parsing
         ↓
2. 根据文件类型分发:
   ├── PDF: 逐页渲染为图片 → VLM 逐页识别
   ├── Word: python-docx 提取文本 → 结构表解析
   └── 图片: 直接送入 VLM
         ↓
3. VLM 识别（Prompt 引导）:
   "从这份体检报告中提取以下信息:
    - 体检者姓名、性别、年龄
    - 检查日期
    - 每个检验项目的名称、结果值、单位、参考区间
    返回严格的 JSON 格式"
         ↓
4. 接收 VLM 返回的结构化 JSON
         ↓
5. 术语标准化:
   将 VLM 识别出的项目名与标准化词表匹配
   非标描述（如"血糖"→"空腹血糖（GLU）"）
   补全 ICD 编码（如适用）
         ↓
6. 数据写入:
   MySQL:
     - report_info: 基本信息
     - report_indicator: 逐项指标（批量写入）
   report_task: status = completed, completed_at = now
         ↓
7. 发布 RabbitMQ 消息:
   {event: "report_parsed", task_id, report_id, hospital_id}
   → AI解读模块 消费此事件
```

### 5.3 状态机

```
排队中 (queued)
   ↓
解析中 (parsing)
   ↓
   ├── 成功 → 解析完成 (completed) → 发布事件
   └── 失败 → retry_count < 3 → 重新入队
                   retry_count >= 3 → 解析失败 (failed) → 通知用户
```

---

## 6. API 接口设计

```
POST   /api/v1/reports/upload          — 上传报告文件（multipart/form-data）
        请求: file + hospital_id
        返回: {task_id, status: "queued"}

GET    /api/v1/reports/tasks/{task_id} — 查询解析任务状态
        返回: {task_id, status, error_message}

GET    /api/v1/reports                 — 报告列表（按用户/医院筛选，分页）
GET    /api/v1/reports/{report_id}     — 报告详情（含所有指标）

DELETE /api/v1/reports/{report_id}     — 删除报告（软删除，同时删除文件）
```

---

## 7. VLM Prompt 设计要点

```
System Prompt:
你是体检报告结构化提取助手。从用户提供的体检报告图片中精确提取信息。
遵守以下规则:
1. 个人信息（姓名、性别、年龄、检查日期）尽可能提取
2. 每个检验项精确提取: 项目名称、结果值、单位、参考区间
3. 不遗漏任何检验项
4. 无法识别的内容标记为 null
5. 仅返回 JSON，不包含任何解释文字

输出格式:
{
  "personal_info": { "name": "张三", "gender": "男", "age": 45, "check_date": "2025-05-17" },
  "indicators": [
    { "item_name": "空腹血糖", "result": "5.2", "unit": "mmol/L", "ref_low": "3.9", "ref_high": "6.1" }
  ]
}
```

---

## 8. 错误处理

| 场景 | 处理方式 |
|------|----------|
| 文件格式不支持 | 上传时返回 400，不创建任务 |
| 文件过大 | 上传时返回 413 |
| 图像模糊度过高 | 预处理阶段返回提示，status = failed |
| PDF 含扫描加密 | VLM 仍可识别（按图片处理），但若为数字加密则提示用户 |
| VLM 识别结果为空 | 重试；3 次后标记 failed，通知用户 |
| VLM 识别部分指标 | 部分成功视为成功，缺失的指标标记为 null |
| 消息队列投递失败 | 本地记录补偿任务，定时扫描补偿 |
