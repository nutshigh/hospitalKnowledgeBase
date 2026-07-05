# KnowledgeGraph 检索融入 RAG 流程

## 概述

在现有 RAG 流程（vector+BM25 融合 → Reranker）中引入 KnowledgeGraph 检索，使用 Neo4j 存储由 CM3KG 数据集构建的医学知识图谱。采用分通道融合策略：文档侧保持现有 vector+BM25+RRF+Reranker 流程不变，KG 侧独立检索，结果按 `source` 字段分区组装后返回给 LLM。

## 背景与现状

现有 RAG 检索流程：

```
query → Vector Retriever (Milvus, top_k=20)
      → BM25 Retriever (docstore, top_k=20)
      → QueryFusionRetriever (RRF)
      → HttpReranker (top_n=5)
      → SearchResult[]
```

关键文件：
- `app/ai/rag/retriever.py` — `RAGRetriever` + `HttpReranker`
- `app/ai/rag/store.py` — `RAGStore`（Milvus + docstore 工厂）
- `app/ai/rag/indexer.py` — `RAGIndexer`（IngestionPipeline 入库）
- `app/ai/rag/__init__.py` — facade：`search()` / `index_documents()` 等
- `app/ai/agents/tools.py` — `search_knowledge` 工具（Agent 调用入口）
- `app/ai/agents/chat_graph.py` / `interp_graph.py` — Agent 使用 `search_knowledge`

## 架构

### 检索流（查询方向）

```
query
  ├── 文档通道（现有，不改）
  │   ├── Vector Retriever (Milvus, top_k=20)
  │   ├── BM25 Retriever (docstore, top_k=20)
  │   └── QueryFusionRetriever (RRF) → HttpReranker (top_n=5) → 文档结果
  │
  └── KG 通道（新增）
      └── KGRetriever → KGClient.search_entities(query, top_k=3) → KG 结果
          ↓
      分区组装: SearchResult[source="document"|"knowledge_graph"]
```

两通道独立运行。KG 通道不可用时（Neo4j 未连接 / CM3KG 未导入 / KG_ENABLED=false），自动退化为纯文档检索，零影响。

### 索引流（一次性导入）

```
CM3KG 数据文件 → scripts/import_cm3kg.py → KGClient.import_cm3kg() → Neo4j
  (节点: 疾病/症状/药物/检查项目等)
  (关系: 疾病-症状/药物-适应症/检查-相关疾病等)
  (索引: 节点名、别名)
```

## 组件设计

### 1. Neo4j 基础设施

在 `infra/docker-compose.yml` 新增 `neo4j` 服务：

```yaml
neo4j:
  image: neo4j:5-community
  container_name: hospital-neo4j
  restart: unless-stopped
  environment:
    NEO4J_AUTH: neo4j/medgraph123
  ports:
    - "7474:7474"   # browser
    - "7687:7687"   # bolt
  volumes:
    - /data/infra/data/neo4j:/data
    - /data/infra/data/neo4j/logs:/logs
```

### 2. 配置 — `app/config.py` + `.env`

新增配置项：

```python
# Neo4j / KnowledgeGraph
NEO4J_URI: str = "bolt://localhost:7687"
NEO4J_USER: str = "neo4j"
NEO4J_PASSWORD: str = "medgraph123"
KG_ENABLED: bool = True           # 总开关
KG_TOP_K: int = 3                 # KG 检索返回数
CM3KG_DATA_DIR: str = "/data/models/CM3KG"
```

### 3. KG 客户端 — `app/ai/rag/kg_client.py`（新文件）

职责：封装 Neo4j 连接 + Cypher 查询 + CM3KG 导入。

```python
class KGClient:
    """Neo4j 知识图谱客户端，单例。"""

    def __init__(self, uri, user, password):
        # 懒连接 neo4j.GraphDatabase.driver

    def is_available(self) -> bool:
        """检查连接 + 图谱是否已导入（节点数 > 0）。"""

    def import_cm3kg(self, data_dir: str) -> int:
        """读取 CM3KG 数据文件，批量写入节点+关系，创建索引。返回导入节点数。"""

    def search_entities(self, query: str, top_k: int = 3) -> list[KGResult]:
        """关键词匹配 CM3KG 节点名/别名 → Cypher 查 1-hop 邻居 → 返回 KGResult 列表。"""
```

**KGResult 数据结构**：
```python
class KGResult(BaseModel):
    entity: str                    # 匹配到的主实体名
    entity_type: str               # 疾病/症状/药物/检查...
    description: str               # 实体描述
    neighbors: list[dict]          # 1-hop 邻居 [{name, type, relation, description}]
    score: float                   # 匹配分数
    text: str                      # 组装好的文本（供 LLM 阅读）
```

`text` 字段格式示例：
```
实体: 高血压 (疾病)
描述: 动脉血压持续高于正常范围的慢性疾病
相关知识:
  - 并发症 → 脑卒中 (疾病): 长期高血压可导致脑血管损伤
  - 常用药物 → 氨氯地平 (药物): 钙通道阻滞剂，用于降压
  - 相关检查 → 血压测量 (检查): 收缩压≥140mmHg或舒张压≥90mmHg
```

**实体匹配策略**（初始版本，简单有效）：
1. 对 query 做分词（jieba 或简单空格/标点分割）
2. 用 Cypher `WHERE n.name CONTAINS $term OR ANY(alias IN n.aliases WHERE alias CONTAINS $term)` 查匹配节点
3. 按 匹配词数 × 节点度数 排序，取 top_k
4. 对每个匹配节点查 1-hop 邻居

### 4. KG 检索器 — `app/ai/rag/kg_retriever.py`（新文件）

职责：将 `KGClient.search_entities` 的结果包装为统一的检索结果。

```python
class KGRetriever:
    """知识图谱检索器，独立于文档检索通道。"""

    def __init__(self, hospital_id: str):
        self._client = kg_client  # 共享单例
        self._top_k = settings.KG_TOP_K

    def retrieve(self, query: str) -> list[SearchResult]:
        """KG 检索，返回 source='knowledge_graph' 的 SearchResult 列表。"""
        if not self._client.is_available():
            return []
        kg_results = self._client.search_entities(query, self._top_k)
        return [
            SearchResult(
                entry_id=None,
                title=r.entity,
                content=r.text,
                category_id=None,
                score=r.score,
                source="knowledge_graph",
            )
            for r in kg_results
        ]
```

### 5. SearchResult 扩展 — `app/ai/rag/types.py`（改）

```python
class SearchResult(BaseModel):
    entry_id: Optional[int] = None
    title: str
    content: str
    category_id: Optional[int] = None
    score: float
    source: str = "document"  # 新增: "document" | "knowledge_graph"
```

### 6. 检索器改造 — `app/ai/rag/retriever.py`（改）

`RAGRetriever.retrieve()` 方法改为分通道：

```python
def retrieve(self, query, category_ids=None, top_k=None) -> list[SearchResult]:
    # --- 文档通道（现有逻辑不变）---
    doc_results = self._retrieve_documents(query, category_ids, top_k)
    # 给文档结果标注 source
    for r in doc_results:
        r.source = "document"

    # --- KG 通道（新增）---
    kg_results = []
    if settings.KG_ENABLED:
        kg_retriever = KGRetriever(self.hospital_id)
        kg_results = kg_retriever.retrieve(query)

    return doc_results + kg_results
```

文档检索逻辑 `_retrieve_documents` 即现有 `retrieve` 的全部代码，提取为私有方法。KG 结果追加在文档结果之后。

### 7. Agent 工具适配 — `app/ai/agents/tools.py`（改）

`search_knowledge` 工具返回时增加 `source` 字段：

```python
@tool
def search_knowledge(query, ...):
    results = ai_rag.search(ctx.hospital_id, query, ...)
    return [
        {"entry_id": r.entry_id, "title": r.title, "content": r.content,
         "score": r.score, "source": r.source}
        for r in results
    ]
```

LLM 看到的 context 自然分区：先文档结果（有 entry_id），后 KG 结果（entry_id=null, source="knowledge_graph"）。

### 8. Middleware 适配 — `chat_graph.py` / `interp_graph.py`（改）

`KnowledgeRefsMiddleware` 只处理 `source="document"` 的结果（有 entry_id 的才累积 knowledge_refs），KG 结果跳过引用累积。

### 9. CM3KG 导入脚本 — `scripts/import_cm3kg.py`（新）

```python
"""一次性脚本：将 CM3KG 数据导入 Neo4j。

用法:
    python scripts/import_cm3kg.py --data-dir /data/models/CM3KG
"""
```

根据 CM3KG 的实际文件格式（下载后确认），解析节点和关系，批量写入 Neo4j。导入完成后创建索引：
- `CREATE INDEX FOR (n:Disease) ON (n.name)`
- `CREATE INDEX FOR (n:Disease) ON (n.aliases)`

### 10. start.sh 更新

`start.sh` 的 Docker 中间件段已包含 Neo4j（compose 文件新增后自动启动）。无需额外启动脚本。

## 数据流总结

### 索引侧（一次性）
```
CM3KG 文件 → import_cm3kg.py → KGClient.import_cm3kg() → Neo4j 节点+关系+索引
```

### 检索侧（每次查询）
```
query
  ├── 文档: vector+BM25 → RRF → Reranker → top5 SearchResult[source="document"]
  └── KG: KGClient.search_entities → top3 SearchResult[source="knowledge_graph"]
       ↓
  合并返回 [doc_results..., kg_results...]
       ↓
  search_knowledge 工具返回 list[dict]（含 source 字段）
       ↓
  LLM 看到分区 context:
    "相关知识:" (文档 chunk)
    "知识图谱:" (实体+关系)
```

## 降级策略

| 场景 | 行为 |
|------|------|
| KG_ENABLED=false | 纯文档检索，与当前完全一致 |
| Neo4j 未启动 | KGClient.is_available()=false，跳过 KG 通道 |
| CM3KG 未导入 | 同上（节点数=0） |
| KG 查询超时/异常 | catch 异常，返回空 KG 结果，不影响文档检索 |

## 不改动的部分

- 知识库文档导入流程（向量索引）：`indexer.py` / `store.py` / `service.py` 不变
- Milvus / Reranker / BGE-M3 / PaddleOCR 不变
- Agent 的图结构（chat_graph / interp_graph 的节点编排）不变
- 前端不变

## 依赖

- `neo4j` Python 驱动（`pip install neo4j`）
- `jieba`（可选，中文分词提升实体匹配）

## CM3KG 数据集 Schema

数据文件：`/data/data/medical.csv`（88590 行，UTF-8，44MB）

每行一个疾病，字段映射为知识图谱：

### 节点类型

| Neo4j Label | 来源字段 | 属性 |
|-------------|---------|------|
| `Disease` | `name` | name, desc, prevent(预防), cause(病因), yibao_status(医保), get_prob(患病率), get_way(传染性), cure_lasttime(治疗周期), cured_prob(治愈率), cost_money(费用) |
| `Symptom` | `symptom` (list) | name |
| `Drug` | `common_drug` + `recommand_drug` (list) | name |
| `Check` | `check` (list) | name |
| `Department` | `cure_department` (list) | name |
| `Treatment` | `cure_way` (list) | name |

### 关系类型

| 关系 | 方向 | 说明 |
|------|------|------|
| `HAS_SYMPTOM` | Disease → Symptom | 疾病的症状 |
| `TREAT_WITH` | Disease → Drug | 常用/推荐药物 |
| `RECOMMEND_CHECK` | Disease → Check | 推荐检查项目 |
| `BELONGS_TO` | Disease → Department | 就诊科室 |
| `TREATMENT_METHOD` | Disease → Treatment | 治疗方式 |
| `ACCOMPANIED_BY` | Disease → Disease | 并发症（acompany 字段） |

### 字段格式

- list 类型字段以 Python literal 格式存储：`"['紫绀', '胸痛', '呼吸困难']"` → `ast.literal_eval` 解析
- 空值为空字符串或 `[]`
- `category` 字段包含分类层级（如 `['疾病百科', '内科', '呼吸内科']`），最后一个元素即科室，与 `cure_department` 冗余，导入时用 `cure_department`
