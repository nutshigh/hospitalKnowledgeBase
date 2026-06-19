# LlamaIndex RAG + LangChain Agent 集成设计

> 日期：2026-06-18
> 状态：设计已确认，待写实现计划
> 范围：用 LlamaIndex 统一管理 RAG 知识库，LangChain（LangGraph）作为 Agent 引擎管理 LLM 交互，完全替换现有手写实现

---

## 背景与动机

现有 RAG 知识库与 AI 交互层是手写实现，存在多处粗糙与缺陷：

**RAG 层问题：**
- `core/milvus.py` 手写 Milvus 封装，collection schema 硬编码
- `core/embedding.py` 手写 OpenAI 兼容 HTTP 客户端
- `core/doc_parser.py` 朴素字符切块（500 字/50 重叠），无语义切分、无元数据抽取、无增量索引
- `knowledge/service.py` 的 `reindex_category` 只删向量不重建，`vector_id` 只是 `str(entry.id)` 无实际意义
- `search` 拿到向量 ID 后回 MySQL 取 content，存在 N+1 查询

**LLM 交互层问题：**
- `core/llm_client.py` 手写 OpenAI 兼容 chat，`interpret_indicator`/`generate_summary` 是手拼 prompt 字符串
- `chat/service.py` 的 `_build_knowledge_context` **只把知识库标题和分数塞进上下文，没塞 content**（明显 bug），且一条消息检索两次知识库（一次拼上下文、一次存 refs）
- `interpretation/service.py` 用 `httpx.post("http://localhost:8000/...")` 走内部 HTTP 调自己的知识库接口（反模式），且对每个非绿区指标单独调一次 LLM，一个报告几十个异常指标 → 几十次 LLM 调用，成本和延迟高

**目标：** 引入 LlamaIndex 统一管理 RAG 知识库（indexing/retrieval/ingestion 全链路），引入 LangGraph 作为 Agent 引擎统一管理 LLM 交互（chat + interpretation 都走 Agent），完全替换上述手写实现。

---

## 关键决策汇总

| # | 决策点 | 选择 |
|---|--------|------|
| 1 | 迁移策略 | 完全替换（A），旧手写封装不留 |
| 2 | 向量库 | 继续 milvus-lite（A），底层换 LlamaIndex MilvusVectorStore，按医院分 collection 不变 |
| 3 | Agent 程度 | 全 Agent（B），chat 和 interpretation 都走 ReAct/function-calling Agent |
| 4 | interpretation 执行模型 | 单报告单 Agent 批量处理（B） |
| 5 | LlamaIndex 管理边界 | 检索 + 文档管理（B），ingestion 全链路交 LlamaIndex，业务元数据留 MySQL |
| 6 | 检索增强 | Reranker + Hybrid Search + Metadata 过滤（A+B+C） |
| 7 | Reranker 部署 | 本地独立服务（A），bge-reranker-v2-m3 |
| 8 | Agent 形态 | LangGraph StateGraph（B） |
| 9 | 落库动作归属 | `save_interpretation` 不给 Agent，走固定节点 |
| 10 | chat knowledge_refs | 累积所有工具调用结果落库（B） |
| 11 | 流式输出 | 只流最终回复 token，工具阶段推状态事件（A） |
| 12 | 会话 Memory | 仍用 MySQL 存消息，LangGraph 无状态跑（A） |
| 13 | 配置切换 | 沿用 local/remote 二选一（A） |
| 14 | Hybrid BM25 实现 | 纯 Python BM25（`llama-index-retrievers-bm25`），保留 milvus-lite，性能瓶颈再切 standalone；引入 IngestionPipeline；锁依赖版本 |

---

## §1 整体架构与依赖

### 1.1 分层与依赖方向

```
┌─────────────────────────────────────────────┐
│  modules/    业务模块（CRUD + 调度）          │
│  knowledge · chat · interpretation · ...     │
├─────────────────────────────────────────────┤
│  ai/         AI 框架集成层（新）              │
│  ├── llm.py         LangChain ChatModel       │
│  ├── rag/           LlamaIndex RAG 全链路     │
│  └── agents/        LangGraph StateGraph      │
├─────────────────────────────────────────────┤
│  core/       纯基础设施（瘦身）               │
│  database · rabbitmq · security · ...        │
└─────────────────────────────────────────────┘
        依赖方向：modules → ai → core
```

- `modules/` 只做业务 CRUD + 编排，不再直接调 pymilvus/httpx LLM
- `ai/` 封装 LlamaIndex + LangGraph，对上暴露高层 API（`index_documents`/`retrieve`/`run_chat_agent`/`run_interpretation_agent`）
- `core/` 删除 `milvus.py`/`embedding.py`/`llm_client.py`/`doc_parser.py` 四个文件，保留 `database.py`/`rabbitmq.py`/`security.py`/`image_preprocess.py`/`vlm_client.py`/`term_normalizer.py`

### 1.2 删除与新增文件

**删除（4 个）：**
- `app/core/milvus.py`
- `app/core/embedding.py`
- `app/core/llm_client.py`
- `app/core/doc_parser.py`

**新增（`app/ai/` 下 9 个 + reranker 服务）：**
```
app/ai/
├── __init__.py
├── config.py              # provider 工厂 + 全局组件单例 + milvus-lite 启动
├── llm.py                 # LangChain ChatOpenAI 封装（含 streaming）
├── rag/
│   ├── __init__.py        # 对外高层 API
│   ├── store.py           # MilvusVectorStore 封装（按医院分 collection）
│   ├── indexer.py         # IngestionPipeline + chunking + embedding + 增量
│   ├── retriever.py       # hybrid(vector+BM25) + reranker + metadata filter
│   └── readers.py         # LlamaIndex readers 适配（PDF/Word/Excel/TXT）
└── agents/
    ├── __init__.py        # 对外高层 API
    ├── tools.py           # @tool 工具集（chat + interp 共享）
    ├── chat_graph.py      # chat StateGraph（含 SSE streaming 适配）
    └── interp_graph.py    # interpretation StateGraph（批量 + 落库节点）

backend/reranker_service/   # 独立 reranker HTTP 服务
├── pyproject.toml
└── main.py
```

**改造（`modules/` 下）：**
- `knowledge/service.py`：删除 `_vectorize_entry`/`search`/`reindex_category` 里的手写 Milvus 调用，改为调 `ai.rag.indexer`/`ai.rag.retriever`
- `knowledge/internal.py`：内部检索接口改为调 `ai.rag.retriever.retrieve()`
- `chat/service.py`：删除 `_build_knowledge_context`/`_get_knowledge_refs`/`process_chat_stream` 的手写 prompt 拼装，改为调 `ai.agents.chat_graph.run()`
- `interpretation/service.py`：删除 `_fetch_knowledge`（httpx 自调反模式）+ 手写 per-indicator LLM 循环，改为调 `ai.agents.interp_graph.run()`
- `interpretation/worker.py`：触发 `interp_graph.run()` 替代旧 `process_interpretation`

### 1.3 依赖（pyproject.toml，锁版本）

新增：
```toml
# LangChain / LangGraph
"langchain-core>=0.3,<0.4",
"langchain-openai>=0.2,<0.3",
"langgraph>=0.2,<0.3",
# LlamaIndex
"llama-index-core>=0.12,<0.13",
"llama-index-vector-stores-milvus>=0.4,<0.5",
"llama-index-readers-file>=0.4,<0.5",
"llama-index-embeddings-openai>=0.3,<0.4",
"llama-index-postprocessor-flag-embedding-reranker>=0.3,<0.4",
"llama-index-retrievers-bm25>=0.5,<0.6",
# Reranker 模型运行时（FlagEmbedding）
"FlagEmbedding>=1.3,<2",
```

保留：`pymilvus`、`milvus-lite`（LlamaIndex MilvusVectorStore 仍走 pymilvus 连 milvus-lite）。
移除：无（现有 httpx 等仍被其他模块用）。

`reranker_service/pyproject.toml` 单独依赖：`fastapi`、`uvicorn`、`FlagEmbedding`、`pydantic`。

### 1.4 配置扩展（config.py）

新增字段：
```python
# Reranker Provider
RERANKER_PROVIDER: str = "local"           # local | remote
RERANKER_BASE_URL: str = "http://localhost:8003"
RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
RERANKER_API_KEY: str = ""                 # remote 时用

# RAG
RAG_CHUNK_SIZE: int = 512
RAG_CHUNK_OVERLAP: int = 72
RAG_VECTOR_TOP_K: int = 20                 # 向量召回数，rerank 前的候选
RAG_FINAL_TOP_K: int = 5                   # rerank 后返回数
RAG_HYBRID_ALPHA: float = 0.5              # vector/BM25 融合权重

# Agent
AGENT_MAX_ITERATIONS: int = 8              # 单轮最大工具调用轮数
```

`LLM_PROVIDER`/`EMBED_PROVIDER`/`VLLM_*`/`REMOTE_*` 全部保留，由 `ai/config.py` 工厂读取后构造 LangChain/LlamaIndex 组件。

---

## §2 RAG 子系统（`ai/rag/`）

### 2.1 `store.py` — 向量存储封装

按医院分 collection，命名沿用现有 `hospital_{hospital_id}_knowledge` 规则，向量数据零迁移（但 schema 变化，需全量 reindex，见 §4.3）。

```python
class RAGStore:
    """按医院隔离的 LlamaIndex MilvusVectorStore 单例工厂"""
    def __init__(self):
        self._stores: dict[str, MilvusVectorStore] = {}
        self._indices: dict[str, VectorStoreIndex] = {}   # 缓存 Index 对象
        self._nodes_cache: dict[str, list] = {}           # BM25 构建用

    def get(self, hospital_id: str) -> MilvusVectorStore:
        if hospital_id not in self._stores:
            self._stores[hospital_id] = MilvusVectorStore(
                uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
                collection_name=f"hospital_{hospital_id}_knowledge",
                dim=VECTOR_DIM,           # 1024，与现有 bge-m3 一致
                overwrite=False,          # 已存在则复用
                metric_type="IP",
                index_params={"index_type":"IVF_FLAT","params":{"nlist":128}},
            )
        return self._stores[hospital_id]

    def get_index(self, hospital_id: str) -> VectorStoreIndex:
        """构造 VectorStoreIndex 并缓存，供 VectorIndexRetriever 使用"""
        if hospital_id not in self._indices:
            self._indices[hospital_id] = VectorStoreIndex(
                vector_store=self.get(hospital_id),
                embed_model=get_embedding_model(),
            )
        return self._indices[hospital_id]

    def get_nodes(self, hospital_id: str) -> list:
        """拉取所有节点供 BM25Retriever 构建，缓存后由 refresh 失效"""
        if hospital_id not in self._nodes_cache:
            self._nodes_cache[hospital_id] = self.get_index(hospital_id).docstore.get_all()
        return self._nodes_cache[hospital_id]

    def refresh(self, hospital_id: str):
        """知识库更新后清缓存，下次 get_* 重建"""
        self._indices.pop(hospital_id, None)
        self._nodes_cache.pop(hospital_id, None)

    def drop(self, hospital_id: str):
        """reindex 用：drop collection 并清缓存"""
        from pymilvus import utility
        utility.drop_collection(f"hospital_{hospital_id}_knowledge")
        self.refresh(hospital_id)
        self._stores.pop(hospital_id, None)

rag_store = RAGStore()
```

- milvus-lite 启动仍由 `ai/config.py` 在应用启动时 `server_manager.start()`（从旧 `milvus.py` 搬过来，不再手写 collection schema——LlamaIndex 自动建）
- `VECTOR_DIM` 由 `ai/config.py` 根据 embedding provider 推断（bge-m3=1024，text-embedding-v3=1024，不一致时启动报错——避免维度错配）

### 2.2 `readers.py` — 文档解析（替换 `core/doc_parser.py`）

用 LlamaIndex readers 替换手写解析，按扩展名路由：

| 扩展名 | Reader | 增强 |
|--------|--------|------|
| `.pdf` | `PyMuPDFReader` | 含表格/版面提取，比旧 `fitz.get_text()` 强 |
| `.docx` | `DocxReader` | 段落级提取 |
| `.xlsx` | `PandasExcelReader` | 每 sheet 一组 Document，元数据带 `sheet_name` |
| `.txt`/`.md` | `SimpleFileReader` | 直接读 |

对外接口：
```python
def load_documents(file_path: str, filename: str) -> list[LlamaIndexDocument]
```
返回的 `Document` 带 metadata：`{"source_file": filename, "file_ext": ext, "sheet_name": ...}`。不再返回 `TextChunk`——chunking 交给 `indexer.py` 的 `IngestionPipeline`。

### 2.3 `indexer.py` — IngestionPipeline（统一入库链路）

```python
class RAGIndexer:
    """文档→chunk→embed→Milvus 的 LlamaIndex IngestionPipeline 封装"""
    # docstore/cache 按 hospital_id 全局共享，跨操作保留以支持增量去重
    _docstores: dict[str, SimpleKVStore] = {}
    _caches: dict[str, IngestionCache] = {}

    def __init__(self, hospital_id: str):
        self.hospital_id = hospital_id
        if hospital_id not in self._docstores:
            self._docstores[hospital_id] = SimpleKVStore()
            self._caches[hospital_id] = IngestionCache()
        self.pipeline = IngestionPipeline(
            transformations=[
                SentenceSplitter(
                    chunk_size=settings.RAG_CHUNK_SIZE,
                    chunk_overlap=settings.RAG_CHUNK_OVERLAP,
                ),
                get_embedding_model(),
            ],
            vector_store=rag_store.get(hospital_id),
            docstore=self._docstores[hospital_id],
            cache=self._caches[hospital_id],
        )

    def index_documents(self, docs: list[Document], category_id: int | None,
                        source_file: str) -> list[str]:
        for d in docs:
            d.metadata.update({
                "category_id": category_id or 0,
                "source_file": source_file,
                "hospital_id": self.hospital_id,
            })
        nodes = self.pipeline.run(documents=docs)
        rag_store.refresh(self.hospital_id)   # 失效 BM25/index 缓存
        return [n.node_id for n in nodes]

    def delete_by_entry(self, entry_id: int):
        rag_store.get(self.hospital_id).delete(filter={"entry_id": entry_id})
        rag_store.refresh(self.hospital_id)

    def reindex_all(self, entries: list[dict]):
        rag_store.drop(self.hospital_id)
        # 重置 docstore/cache（旧内容 hash 已无意义）
        self._docstores[self.hospital_id] = SimpleKVStore()
        self._caches[self.hospital_id] = IngestionCache()
        self.pipeline.docstore = self._docstores[self.hospital_id]
        self.pipeline.cache = self._caches[self.hospital_id]
        for e in entries:
            docs = [Document(text=e["content"], metadata={
                "entry_id": e["id"], "category_id": e["category_id"] or 0,
                "title": e["title"], "source_file": e["source_file"] or "",
            })]
            self.pipeline.run(documents=docs)
        rag_store.refresh(self.hospital_id)
```

**关键点：**
- **metadata 标准化**：每个 node 带 `entry_id`（关联 MySQL `KnowledgeEntry.id`）、`category_id`、`title`、`source_file`、`hospital_id`。LlamaIndex 的 metadata filter 直接用这些字段。
- **增量索引**：IngestionPipeline 的 docstore cache 按 content hash 去重，同一文件重复 import 不重复 embedding（现状每次都重 embed）。
- **`KnowledgeEntry.content` 仍存 MySQL**（业务元数据 + 文本备份），但 chunk 文本和 vector_id 不再存 MySQL——`vector_id` 列废弃（保留列不删，避免 schema 迁移），新代码不读写它。chunk 内容由 LlamaIndex docstore 管理。
- `knowledge/service.py` 的 `create_entry`/`update_entry`/`delete_entry`/`import_from_file` 改为：MySQL 存业务条目 → 调 `RAGIndexer.index_documents()` 入库向量。

### 2.4 `retriever.py` — Hybrid 检索 + Reranker

```python
class RAGRetriever:
    """向量 + BM25 融合检索 → reranker 重排 → 返回结构化结果"""
    def __init__(self, hospital_id: str):
        self.hospital_id = hospital_id
        self._vector = VectorIndexRetriever(
            index=rag_store.get_index(hospital_id),
            similarity_top_k=settings.RAG_VECTOR_TOP_K,
        )
        self._bm25 = BM25Retriever.from_nodes(
            rag_store.get_nodes(hospital_id),
            similarity_top_k=settings.RAG_VECTOR_TOP_K,
        )
        self._fusion = QueryFusionRetriever(
            [self._vector, self._bm25],
            similarity_top_k=settings.RAG_VECTOR_TOP_K,
            mode="reciprocal_rerank",
        )
        self._reranker = HttpReranker(top_n=settings.RAG_FINAL_TOP_K)

    def retrieve(self, query: str, category_ids: list[int] | None = None,
                 top_k: int | None = None) -> list[SearchResult]:
        filters = None
        if category_ids:
            filters = MetadataFilters(filters=[
                MetadataFilter(key="category_id", value=category_ids, operator="IN")
            ])
        nodes = self._fusion.retrieve(query, filters=filters)
        nodes = self._reranker.postprocess_nodes(nodes, query_str=query)
        if top_k:
            nodes = nodes[:top_k]
        return [SearchResult(
            entry_id=n.metadata["entry_id"],
            title=n.metadata["title"],
            content=n.text,
            category_id=n.metadata.get("category_id"),
            score=n.score,
        ) for n in nodes]
```

**关键点：**
- **BM25 索引是进程内 in-memory**（`llama-index-retrievers-bm25`），首次按 hospital_id 懒加载所有节点构建，后续复用；知识库更新后通过 `refresh()` 重建（在 indexer 里触发）。
- **metadata filter 在 fusion retriever 之上应用**——LlamaIndex 的 `QueryFusionRetriever` 是否原生支持 `filters` 参数需在 plan 阶段验证；若不支持，降级方案为：在 vector retriever 和 BM25 retriever 各自构造时传入 `filters`，再送入 fusion。spec 层面明确"支持 category_ids 过滤"这一功能要求不变。
- **reranker 走本地服务**：自定义 `HttpReranker`（`BaseNodePostprocessor` 子类）调外部 reranker 服务，避免 backend 进程占显存。
- **降级**：reranker 服务不可用时 catch 异常跳过重排，直接返回 fusion 结果（top_k 截断）。BM25 构建失败时退化为纯向量检索。保留现状"embedding 不可用时跳过 RAG"的降级语义。

### 2.5 Reranker 服务

独立 Python 服务，进程外起，HTTP API：

```
POST /rerank
{"query": "...", "documents": ["...", "..."], "top_n": 5}
→ {"results": [{"index":0,"score":0.92,"document":"..."}, ...]}
```

- 用 `FlagEmbedding` 加载 `bge-reranker-v2-m3`，FastAPI 暴露 `/rerank`
- 单独项目目录 `backend/reranker_service/`（自己的 `pyproject.toml`），`start_local.sh` 里加一行启动
- LlamaIndex 侧自定义 `HttpReranker`（`BaseNodePostprocessor` 子类）调这个 HTTP，放 `ai/rag/retriever.py` 里

### 2.6 对外暴露的简化 API

`ai/rag/__init__.py` 提供四个高层函数，`modules/` 只调这些：

```python
def index_documents(hospital_id, docs, category_id, source_file) -> list[str]
def delete_vectors(hospital_id, entry_id: int) -> None
def search(hospital_id, query, category_ids=None, top_k=None) -> list[SearchResult]
def reindex_hospital(hospital_id, entries: list[dict]) -> None
```

`modules/knowledge/service.py` 和 `modules/knowledge/internal.py` 只用这四个函数，不直接接触 LlamaIndex 类。`modules/chat` 和 `modules/interpretation` 不直接调 RAG，而是通过 Agent 工具 `search_knowledge` 间接调（§3）。

---

## §3 Agent 子系统（`ai/agents/`）

### 3.1 `llm.py` — LangChain ChatModel 封装

```python
from langchain_openai import ChatOpenAI
from app.config import settings

def get_chat_model(streaming: bool = False) -> ChatOpenAI:
    if settings.LLM_PROVIDER == "remote":
        return ChatOpenAI(
            base_url=settings.REMOTE_LLM_BASE_URL,
            model=settings.REMOTE_LLM_MODEL,
            api_key=settings.REMOTE_LLM_API_KEY,
            temperature=settings.REMOTE_LLM_TEMPERATURE,
            max_tokens=settings.REMOTE_LLM_MAX_TOKENS,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            streaming=streaming,
        )
    return ChatOpenAI(
        base_url=settings.VLLM_BASE_URL,
        model=settings.VLLM_CHAT_MODEL,
        api_key="not-required",
        temperature=0.1,
        max_tokens=1024,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        streaming=streaming,
    )
```

- 现状 `interpret_indicator`/`generate_summary` 这种手拼 prompt 方法全部删除，交给 Agent 图里的 prompt 节点。
- 流式由 LangGraph 在图执行时通过 `astream_events` 暴露（§3.4）。

### 3.2 `tools.py` — 共享工具集

`@tool` 装饰器定义，chat 和 interp 图共享。每个工具是纯函数，不持有状态，依赖（db、hospital_id）通过闭包绑定。

| 工具 | 签名 | 实现 |
|------|------|------|
| `search_knowledge` | `(query: str, category_ids: list[int]? = None, top_k: int? = None) -> list[dict]` | 调 `ai.rag.search(hospital_id, ...)`，返回 `[{entry_id,title,content,score}]` |
| `get_report_indicators` | `(report_id: int) -> list[dict]` | 查 `report_indicator` 表 |
| `get_report_summary` | `(report_id: int) -> dict` | 查 `report_info` + 关联 `report_interpretation` 概览 |
| `get_user_history_reports` | `(user_id: int, limit: int = 5) -> list[dict]` | 查用户历年报告概览 |
| `get_indicator_history` | `(user_id: int, item_name: str) -> list[dict]` | 单指标历史趋势 |
| `get_triage_rules` | `() -> list[dict]` | 查 `triage_rule` 表，让 Agent 知晓当前规则语义 |

**hospital_id / db 注入方式：**
```python
def make_tools(hospital_id: str, db_session: Session) -> list[BaseTool]:
    @tool
    def search_knowledge(query: str, category_ids: list[int] | None = None,
                         top_k: int | None = None) -> list[dict]:
        results = rag.search(hospital_id, query, category_ids, top_k)
        return [{"entry_id": r.entry_id, "title": r.title,
                 "content": r.content, "score": r.score} for r in results]
    return [search_knowledge, get_report_indicators, ...]
```

工具集闭包绑定 `hospital_id`，避免在 prompt 里传上下文参数污染 LLM 工具选择。`db_session` 由调用方（router/worker）创建并传入，图跑完随 session 关闭。

`save_interpretation` 不作为工具（决策 #9），落库走固定节点。

### 3.3 `chat_graph.py` — Chat Agent StateGraph

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class ChatState(TypedDict):
    hospital_id: str
    session_id: int
    user_id: int
    report_id: int | None
    messages: Annotated[list, add_messages]
    knowledge_refs: Annotated[list[dict], lambda a,b: a+b]
    final_response: str

def build_chat_graph(hospital_id: str, db: Session) -> CompiledGraph:
    tools = make_tools(hospital_id, db)
    model = get_chat_model(streaming=True).bind_tools(tools)
    sys_prompt = CHAT_SYSTEM_PROMPT

    def agent_node(state: ChatState):
        sys = _build_system_with_report(state)
        msgs = [SystemMessage(sys)] + state["messages"]
        resp = model.invoke(msgs)
        return {"messages": [resp]}

    def tool_node(state: ChatState):
        # 执行工具调用，累积 knowledge_refs
        ...

    def should_continue(state: ChatState) -> str:
        last = state["messages"][-1]
        if last.tool_calls: return "tools"
        return END

    g = StateGraph(ChatState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tool_node)
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", should_continue)
    g.add_edge("tools", "agent")
    return g.compile()
```

**执行流程（`run` 函数，由 `chat/service.py` 调用）：**
1. 从 MySQL 拉 session 的历史消息 → 注入 `state["messages"]`
2. 加载 report_id 对应报告数据（不塞进 system，而是让 Agent 用 `get_report_indicators` 工具按需取——但首条消息时预注入报告概览到 system prompt，避免 Agent 每轮都调工具拿基础信息）
3. `graph.astream_events({"messages": [HumanMessage(user_msg)], ...})` 流式跑
4. 工具调用阶段推 `tool_status` 事件（§3.4），最终 LLM 生成时推 token
5. 跑完取 `final_response` + `knowledge_refs` → 落库 `ChatMessage`

**会话历史管理（决策 #12）：** 图无状态，每次从 MySQL 加载 history 注入；不引入 checkpointer。`MAX_HISTORY_ROUNDS=20` 保留。

**knowledge_refs 累积（决策 #10）：** `tool_node` 里识别 `search_knowledge` 调用，把返回的 `{entry_id, title}` 追加到 `state["knowledge_refs"]`（用 reducer 累积）。图跑完一次性落库到 assistant 消息的 `knowledge_refs` 列。

**并发控制：** 现有 `_session_locks` 机制保留，在 `run` 入口加锁。

### 3.4 SSE 流式适配（`chat/stream.py` 改造）

LangGraph 的 `astream_events` 产出多种事件，映射为现有 SSE 事件：

| LangGraph 事件 | SSE 事件 | data |
|----------------|----------|------|
| `on_tool_start` | `tool_status` | `{"tool":"search_knowledge","status":"start"}` |
| `on_tool_end` | `tool_status` | `{"tool":"search_knowledge","status":"end","count":3}` |
| `on_chat_model_stream`（最终回复） | `token` | `{"content": "..."}` |
| 图结束 | `done` | `{"message_id": 123}` |
| 任何异常 | `error` | `{"message": "..."}` |

**决策 #11：只流最终回复 token。** LangGraph 的 `astream_events` 不直接区分"中间 tool-calling LLM 调用"和"最终回复 LLM 调用"。实现策略：
- 所有 agent_node 都用 `model.stream()`，但只把"无 tool_calls 的那次"的 token 推给前端。
- tool_node 执行后回到 agent_node，agent_node 拿到 LLM 输出，如果 `tool_calls` 为空则推 token，否则不推。
- 在 `astream_events` 层面监听 `on_chat_model_stream`，结合 state 里"是否还有后续工具调用"判断是否转发。

具体实现细节在 plan 阶段定，spec 层面明确：**最终回复 token 流给前端，中间 tool-calling LLM 输出不流。**

### 3.5 `interp_graph.py` — Interpretation Agent StateGraph

**决策 #4：单报告单 Agent 批量处理。** 图结构：

```
load_indicators → rules_engine → filter_abnormal → agent_batch → persist → END
                                                       ↑    ↓
                                                       └─tools─┘
```

```python
class InterpState(TypedDict):
    hospital_id: str
    report_id: int
    indicators: list[dict]
    judgments: list[dict]
    abnormal_indicators: list[dict]
    agent_explanations: dict[int, str]
    knowledge_refs: dict[int, list[dict]]
    overall_level: str
    red_count: int; yellow_count: int; green_count: int
```

**节点定义：**

1. **`load_indicators`**：从 `report_indicator` 表加载所有指标，连同年对比数据。纯 DB 查询，无 LLM。

2. **`rules_engine`**：调用现有 `rules_engine.evaluate()` 对每个指标算 `color_level`/`deviation`/`matched_rule_id`。纯确定性，无 LLM（决策 #9）。产出 `judgments`。

3. **`filter_abnormal`**：过滤出 `color_level in ("red","yellow")` 的指标 → `abnormal_indicators`。绿区直接落库无解读（沿用现状）。

4. **`agent_batch`**：核心 Agent 节点。把所有异常指标一次性喂给 LLM：
   ```
   system: INTERP_SYSTEM_PROMPT
   user: "以下是本报告的异常指标，请对每个查相关医学知识并生成解读+建议：
          [指标1: ALT, 值85, 偏高, 红区]
          [指标2: 空腹血糖, 值7.2, 偏高, 黄区]
          ...
          对每个指标输出 JSON: {indicator_id, explanation, suggestion}"
   ```
   Agent 通过 `search_knowledge` 工具批量查知识库（可能一次查多个 query），LLM 生成所有指标的解读。用 structured output 强制 JSON schema（`InterpBatchResult`），避免解析自由文本。**注意：** `with_structured_output` 与 `bind_tools` 在 LangChain 里可能冲突（前者本身占用 function-calling 通道），具体实现方式（分开两次调用 / 用 `Runnable.with_structured_output` 的 `method="json_mode"` / 自定义 output parser）在 plan 阶段定。spec 层面明确功能要求：Agent 能调工具查知识库，且最终输出为可解析的结构化 JSON。

5. **`persist`**：固定落库节点（决策 #9）。写 `IndicatorJudgment`（含 `knowledge_refs`）+ 更新 `ReportInterpretation` 状态/计数/overall_level + 发 RabbitMQ 完成事件。无 LLM，纯 DB 写。

**工具使用：** `agent_batch` 节点同样用 `make_tools()` 构造的工具集，Agent 自主决定调几次 `search_knowledge`（可能对多个指标分别查，或一次查综合 query）。

**批处理失败处理：** Agent 节点失败 → 整图失败 → `ReportInterpretation.status="failed"`，retry_count++，沿用现有重试机制（≥3 次永久失败）。

**触发方式：** `interpretation/worker.py` 消费 RabbitMQ 任务 → 创建 db session → 调 `run_interpretation_agent(hospital_id, report_id)`。worker 仍是 RabbitMQ 消费者，只换内部调用。

### 3.6 对外暴露 API

`ai/agents/__init__.py`：
```python
async def run_chat_agent(hospital_id, db, session, user_message, user_id) -> AsyncIterator[SSEEvent]
def run_interpretation_agent(hospital_id, db, report_id) -> dict
```

- `modules/chat/service.py` 的 `process_chat_stream` 改为调 `run_chat_agent` 并把 SSE 事件转给 `stream.py`
- `modules/interpretation/service.py` 的 `process_interpretation` 改为调 `run_interpretation_agent`
- 旧的 `_fetch_knowledge`（httpx 自调）、`interpret_indicator`、`generate_summary` 全部删除

### 3.7 Prompt 设计要点

- **Chat system prompt**：沿用现有 `CHAT_SYSTEM_PROMPT` 核心规则（6 条），追加工具使用指引："你有以下工具可用：search_knowledge 查医学知识库、get_report_indicators 查报告指标…优先用工具获取信息，不要凭空回答。"
- **Interp system prompt**：沿用现有 `llm_client.py:SYSTEM_PROMPT` 核心规则（5 条），追加批量处理指令："对每个异常指标生成 explanation（解读）和 suggestion（建议），引用知识库注明来源，危急值提示立即就医。"
- 具体措辞在 plan 阶段定稿，spec 阶段明确规则不变、只增工具指引。

---

## §4 迁移影响与边界

### 4.1 受影响模块清单

| 模块 | 改动程度 | 说明 |
|------|---------|------|
| `core/milvus.py` | 删除 | 功能搬入 `ai/rag/store.py` + `ai/config.py` 启动逻辑 |
| `core/embedding.py` | 删除 | 功能由 `ai/config.py` 的 `get_embedding_model()` 工厂替代 |
| `core/llm_client.py` | 删除 | 功能由 `ai/llm.py` + Agent 图替代 |
| `core/doc_parser.py` | 删除 | 功能由 `ai/rag/readers.py` 替代 |
| `modules/knowledge/service.py` | 改造 | 删 `_vectorize_entry`/手写 search/reindex，改调 `ai.rag.*` |
| `modules/knowledge/internal.py` | 改造 | `search` 改调 `ai.rag.search()` |
| `modules/knowledge/router.py` | 无改动 | HTTP 接口契约不变 |
| `modules/chat/service.py` | 改造 | 删手写 prompt 拼装，改调 `ai.agents.run_chat_agent` |
| `modules/chat/stream.py` | 改造 | SSE 事件类型增加 `tool_status`，`token`/`done`/`error` 保留 |
| `modules/chat/router.py` | 无改动 | 接口契约不变 |
| `modules/chat/models.py` | 无改动 | 表结构不变，`knowledge_refs` 列复用 |
| `modules/interpretation/service.py` | 改造 | 删 `process_interpretation` 主体 + `_fetch_knowledge` + per-indicator LLM 循环，改调 `ai.agents.run_interpretation_agent` |
| `modules/interpretation/worker.py` | 改造 | 消费任务后调 `run_interpretation_agent` |
| `modules/interpretation/rules_engine.py` | 无改动 | 纯确定性规则引擎，被 interp 图调用 |
| `modules/interpretation/router.py` | 无改动 | 接口契约不变 |
| `modules/interpretation/models.py` | 无改动 | 表结构不变，`IndicatorJudgment.knowledge_refs` 列复用 |
| `modules/knowledge/models.py` | 无改动 | `KnowledgeEntry.vector_id` 列保留不删，新代码不读写 |
| `config.py` | 扩展 | 新增 reranker/RAG/Agent 配置字段（§1.4） |
| `pyproject.toml` | 扩展 | 新增 9 个依赖（§1.3） |

### 4.2 对外 API 契约不变

**HTTP 接口**全部保持现状，前端零改动：
- `/api/v1/knowledge/*`（CRUD/import/reindex/search）— 入参出参 schema 不变
- `/api/v1/knowledge/internal/search` — 不变，仍供内部调用（但 interpretation 改走 Agent 工具，不再调这个 HTTP）
- `/api/v1/chat/sessions*`/`/messages` — 不变，SSE 事件新增 `tool_status`（前端可选渲染，不渲染也不报错，因为 `event:` 类型前端可忽略）
- `/api/v1/interpretations/*` — 不变

**SSE 事件兼容性：** 现状前端处理 `token`/`done`/`error` 三种事件。新增 `tool_status` 是可选增强——前端不监听则自动忽略（SSE 规范如此），不破坏现有行为。前端若想显示"正在检索知识库…"再增加监听即可。

### 4.3 向后兼容的数据策略

| 数据 | 处理 |
|------|------|
| MySQL `knowledge_entry.content` | 保留，仍存全文，作为业务元数据 + 文本备份 |
| MySQL `knowledge_entry.vector_id` | 保留列，新代码不读写（已入库的旧值无害，reindex 后无意义） |
| MySQL `chat_*`/`report_interpretation`/`indicator_judgment` | 表结构不变，数据不迁移 |
| Milvus 旧向量数据 | 需要一次全量 reindex——旧 collection 的 schema 是手写的（`id` auto_id + `entry_id` INT64 等），LlamaIndex 的 `MilvusVectorStore` schema 不同（用 node_id 字符串主键）。迁移步骤：drop 旧 collection → 调 `reindex_hospital(hospital_id, entries)` 从 MySQL 重建。这是迁移唯一的数据操作，放在迁移脚本里 |

### 4.4 不做范围外的事（YAGNI）

明确不在本次改造范围：
- 多模态知识库（图片走 VLM 描述后入库）—— 决策 #6 的 D 项，后续迭代
- LangGraph checkpointer 持久化 —— 决策 #12 确认不用
- 经典 AgentExecutor —— 决策 #8 确认用 LangGraph
- `save_interpretation` 作为工具 —— 决策 #9 确认走固定节点
- per-indicator Agent —— 决策 #4 确认单报告单 Agent
- Milvus standalone 迁移 —— 决策 #14 确认先用纯 Python BM25，遇到性能瓶颈再说
- 知识库分类/条目 CRUD 改用 LlamaIndex DocumentStore —— 决策 #5 确认业务元数据留 MySQL

### 4.5 启动流程变化

`start_local.sh` / `start.sh` / `start_windows_local.bat` 增加：
```bash
# 启动 reranker 服务（后台）
cd backend/reranker_service && uv run uvicorn main:app --port 8003 --host 127.0.0.1 &
cd ../..
```

后端 `app/main.py` 启动时 `ai/config.py` 负责：
1. 启动 milvus-lite server（从旧 `milvus.py` 搬）
2. 预构造默认 embedding 模型（验证 provider 可达，失败时启动报错）
3. 不预构造 LLM/Agent 图——按 hospital_id 懒加载（避免启动时连不上 vLLM 导致整个后端起不来）

### 4.6 测试策略

- **RAG 层单元测试**：`ai/rag/` 各模块独立测试——indexer 用临时 Milvus collection，retriever 用 mock nodes，readers 用样本文件
- **Agent 层单元测试**：`ai/agents/tools.py` 每个工具独立测试（mock db）；chat_graph/interp_graph 用 mock LLM 跑完整图验证状态流转
- **集成测试**：知识库 CRUD → import 文件 → search 全链路；chat 发消息 → 收到 SSE 事件；interpretation 触发 → 落库解读
- **现有测试**：检索现有测试文件后再定具体适配

---

## 附：关键文件引用

- 现状 chat 实现：`backend/app/modules/chat/service.py:174`（`process_chat_stream`，含 RAG 上下文 bug）
- 现状 knowledge 检索：`backend/app/modules/knowledge/service.py:168`（`search`，N+1 查询）
- 现状 interpretation 自调 HTTP：`backend/app/modules/interpretation/service.py:173`（`_fetch_knowledge`）
- 现状 LLM 客户端：`backend/app/core/llm_client.py:109`（`interpret_indicator`，手拼 prompt）
- 现状 Milvus 封装：`backend/app/core/milvus.py:10`（手写 schema）
- 现状文档解析：`backend/app/core/doc_parser.py:69`（朴素字符切块）
- 现状配置：`backend/app/config.py:4`（`Settings`，待扩展）
