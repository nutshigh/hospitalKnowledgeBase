# 模块01：知识库模块 — 详细设计

## 在整体架构中的位置

```
接入层 → API网关 → 业务模块层
                      ├── 知识库模块 ← 本文档
                      ├── 报告解析模块
                      ├── AI解读模块
                      ├── 统计分析模块
                      └── 调度管理模块
                              ↓
                        基础设施层 → 数据层
```

知识库模块是 AI 解读模块的上游依赖，负责医疗知识的存取、向量化与检索，为 LLM 生成解读提供"有据可依"的医学上下文。

---

## 1. 功能清单

| 编号 | 功能 | 说明 |
|------|------|------|
| K1 | 知识条目 CRUD | 创建、编辑、删除、查询医疗知识条目，支持分类管理 |
| K2 | 文档导入 | 解析 PDF/Word/Excel/文本格式的医疗文档，自动分段入库 |
| K3 | 向量化流水线 | 文档分段 → Embedding → 写入 Milvus 命名空间 |
| K4 | 向量检索服务 | 接收检索 Query，在对应医院命名空间内做语义检索，返回 Top-K 结果 |
| K5 | 知识实时更新 | 单条增/改/删后局部更新向量索引，无需全量重建 |
| K6 | 知识分类管理 | 按疾病、检验指标、临床路径等维度分类组织知识 |
| K7 | 检索质量监控 | 记录检索命中率、召回率，标记"知识缺失"日志供后续补充 |

---

## 2. 模块依赖关系

```
知识库模块
  │
  ├── 被依赖: AI解读模块（检索接口）
  │
  ├── 依赖基础设施层:
  │     ├── Milvus（向量存储与检索）
  │     └── 文件存储（导入文档的原始文件存档）
  │
  └── 依赖数据层:
        └── MySQL（知识条目元数据、分类、更新日志）
```

| 方向 | 模块/组件 | 依赖内容 |
|------|-----------|----------|
| 被依赖 | AI 解读模块 | 调用知识检索接口获取医学上下文 |
| 调用 | Milvus | 向量写入、向量检索 |
| 调用 | MySQL | 知识条目表、分类表存储 |
| 调用 | 文件存储 | 导入的源文档归档 |

---

## 3. 技术栈

| 类别 | 选型 | 用途 |
|------|------|------|
| 后端框架 | FastAPI | 提供知识管理 REST API |
| 数据库 | MySQL | 存储知识条目、分类、元数据 |
| 向量数据库 | Milvus | 存储知识向量，按医院命名空间隔离 |
| Embedding 模型 | 本地私有化部署（如 BGE-M3） | 文档向量化 |
| 文档解析 | PyMuPDF / python-docx / openpyxl | 解析 PDF/Word/Excel |
| 文本分段 | 自研分段器（按标题/段落切分） | 保留文档结构 |

---

## 4. 数据库设计

### 4.1 SQL 表结构（每家医院独立数据库）

**knowledge_category — 知识分类表**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| hospital_id | VARCHAR(32) | 医院标识（冗余，需与所属数据库一致） |
| name | VARCHAR(100) | 分类名称 |
| parent_id | BIGINT | 父分类 ID，支持树形结构 |
| sort_order | INT | 排序 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

**knowledge_entry — 知识条目表**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| hospital_id | VARCHAR(32) | 医院标识 |
| category_id | BIGINT FK | 所属分类 |
| title | VARCHAR(200) | 知识标题 |
| content | TEXT | 知识正文 |
| source_type | VARCHAR(20) | 来源类型：manual / import |
| source_file | VARCHAR(500) | 导入源文件路径（可为空） |
| chunk_index | INT | 分块序号（同一文档多段时） |
| parent_entry_id | BIGINT | 父条目 ID（分块的归属） |
| vector_id | VARCHAR(64) | Milvus 中对应向量 ID |
| status | TINYINT | 1=active, 0=deleted |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### 4.2 Milvus Collection 结构

```
Collection: hospital_{hospital_id}_knowledge
  Fields:
    - id (INT64, PK)
    - vector (FLOAT_VECTOR, dim=1024)
    - entry_id (INT64)        ← 关联 knowledge_entry.id
    - category_id (INT64)     ← 知识分类
    - title (VARCHAR)
    - source_file (VARCHAR)
    - created_at (INT64)      ← unix timestamp
  Index: IVF_FLAT / HNSW
```

---

## 5. 处理流程

### 5.1 知识导入流程

```
1. 医生端上传文件或输入文本
         ↓
2. 文档解析引擎判断格式
   ├── PDF → PyMuPDF 提取文本
   ├── Word → python-docx 提取文本
   ├── Excel → openpyxl 逐行读取
   └── 纯文本 → 直接进入分段
         ↓
3. 文本分段器：按标题/段落切分为 chunk
   每 chunk ≤ 512 token，保留上下文重叠窗口（50 token）
         ↓
4. 逐 chunk 调用 Embedding 模型生成向量（维度：1024）
         ↓
5. 批量写入：
   ├── MySQL: 写入 knowledge_entry 记录（含 vector_id）
   └── Milvus: 写入对应医院命名空间的向量
         ↓
6. 返回导入结果（导入条目数、耗时）
```

### 5.2 知识检索流程（供 AI 解读模块调用）

```
1. AI 解读模块传入:
   - hospital_id
   - query_text（由异常指标拼接的检索语句）
   - top_k（默认 5）
         ↓
2. 调用 Embedding 模型将 query_text 向量化
         ↓
3. Milvus 检索：
   在 hospital_{hospital_id}_knowledge 命名空间内
   按向量相似度搜索，返回 top_k 条结果
         ↓
4. 根据返回的 entry_id 从 MySQL 回查完整内容
         ↓
5. 组装返回:
   [
     {title, content, category, source_file, score},
     ...
   ]
```

### 5.3 知识更新流程

```
单条修改:
  1. 修改 MySQL 中 knowledge_entry.content
  2. 重新向量化新 content
  3. Milvus: 按 vector_id 删除旧向量 → 插入新向量

单条删除:
  1. MySQL: knowledge_entry.status = 0（软删除）
  2. Milvus: 按 vector_id 删除向量

局部重建:
  1. 选择要重建的分类
  2. 删除 Milvus 中该分类的所有向量
  3. 重新向量化 MySQL 中该分类的所有条目
  4. 批量写入 Milvus
  5. 全程不影响其他分类的检索
```

---

## 6. API 接口设计

### 对外暴露（供 AI 解读模块调用）

```
POST /api/v1/knowledge/search
  Request:
    {
      "hospital_id": "H001",
      "query": "空腹血糖偏高，参考值3.9-6.1",
      "top_k": 5,
      "category_ids": [1, 2]       // 可选，限定分类
    }
  Response:
    {
      "results": [
        {
          "entry_id": 123,
          "title": "空腹血糖异常临床解读",
          "content": "...",
          "category": "检验指标解读",
          "score": 0.92
        }
      ]
    }
```

### 对内暴露（供医生端调用）

```
GET    /api/v1/knowledge/entries         — 知识列表（分页、按分类筛选）
POST   /api/v1/knowledge/entries         — 手动创建知识条目
GET    /api/v1/knowledge/entries/{id}    — 查看知识详情
PUT    /api/v1/knowledge/entries/{id}    — 编辑知识条目
DELETE /api/v1/knowledge/entries/{id}    — 删除知识条目（软删除）

GET    /api/v1/knowledge/categories      — 获取分类树
POST   /api/v1/knowledge/categories      — 创建分类
PUT    /api/v1/knowledge/categories/{id} — 编辑分类
DELETE /api/v1/knowledge/categories/{id} — 删除分类

POST   /api/v1/knowledge/import          — 上传文档导入（multipart/form-data）
GET    /api/v1/knowledge/import/status/{task_id} — 查询导入进度

POST   /api/v1/knowledge/reindex/{category_id}   — 触发局部重建索引
```

---

## 7. 错误处理

| 场景 | 处理方式 |
|------|----------|
| 文档格式不支持 | 返回 400，提示支持的格式列表 |
| 文档解析失败 | 返回具体失败原因（文件损坏/加密/空白页），不写入数据库 |
| Embedding 调用超时 | 单条失败则跳过并记录，整体导入完成后汇总失败列表 |
| 检索无结果（score 均低于阈值） | 返回空列表，AI 解读模块负责处理"无知识上下文"分支 |
| Milvus 连接失败 | 返回 503，不影响 MySQL 层面知识 CRUD |
