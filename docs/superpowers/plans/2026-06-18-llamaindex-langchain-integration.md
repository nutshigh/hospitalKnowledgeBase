# LlamaIndex RAG + LangGraph Agent 集成实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完全替换手写 RAG 知识库和 LLM 交互层，引入 LlamaIndex 管理 RAG 全链路、LangGraph 作为 Agent 引擎管理 chat 和 interpretation 的 LLM 交互。

**Architecture:** 新增 `app/ai/` 顶层包（`rag/` + `agents/` + `llm.py` + `config.py`），分层依赖 `modules → ai → core`。删除 `core/milvus.py`/`embedding.py`/`llm_client.py`/`doc_parser.py` 四个手写文件。RAG 用 LlamaIndex IngestionPipeline + MilvusVectorStore + hybrid retrieval(vector+BM25) + reranker。Agent 用 LangGraph StateGraph，chat 和 interpretation 共享工具集。对外 HTTP 接口契约不变，前端零改动。

**Tech Stack:** Python 3.12, FastAPI, LlamaIndex 0.12, LangChain 0.3, LangGraph 0.2, pymilvus/milvus-lite, FlagEmbedding (bge-reranker-v2-m3), pytest

**Spec:** `docs/superpowers/specs/2026-06-18-llamaindex-langchain-integration-design.md`

## Global Constraints

- Python >=3.12，包管理用 uv
- 锁依赖版本：`langchain-core>=0.3,<0.4`、`langchain-openai>=0.2,<0.3`、`langgraph>=0.2,<0.3`、`llama-index-core>=0.12,<0.13`、`llama-index-vector-stores-milvus>=0.4,<0.5`、`llama-index-readers-file>=0.4,<0.5`、`llama-index-embeddings-openai>=0.3,<0.4`、`llama-index-postprocessor-flag-embedding-reranker>=0.3,<0.4`、`llama-index-retrievers-bm25>=0.5,<0.6`、`FlagEmbedding>=1.3,<2`
- 向量库继续 milvus-lite（嵌入式），按医院分 collection，命名 `hospital_{hospital_id}_knowledge`
- Embedding 维度 1024（bge-m3），embedding provider 启动时校验维度
- 配置沿用 `LLM_PROVIDER=local|remote` / `EMBED_PROVIDER=local|remote` 二选一模式
- 对外 HTTP 接口契约不变，SSE 新增 `tool_status` 事件（前端可忽略）
- MySQL 表结构不变，`knowledge_entry.vector_id` 列保留但新代码不读写
- 测试用 pytest，外部依赖（Milvus/vLLM/MySQL/RabbitMQ）用 mock
- 中文 system prompt 规则不变，只增工具使用指引
- reranker 是独立 HTTP 服务（端口 8003），不嵌入 backend 进程

---

## File Structure

### 新增文件

| 文件 | 职责 |
|------|------|
| `backend/app/ai/__init__.py` | 包初始化 |
| `backend/app/ai/config.py` | provider 工厂（LLM/Embedding/Reranker）+ milvus-lite 启动 + 全局单例 |
| `backend/app/ai/llm.py` | LangChain ChatOpenAI 封装 |
| `backend/app/ai/rag/__init__.py` | 对外高层 API：`index_documents`/`delete_vectors`/`search`/`reindex_hospital` |
| `backend/app/ai/rag/store.py` | RAGStore：按医院分 collection 的 MilvusVectorStore 工厂 |
| `backend/app/ai/rag/readers.py` | LlamaIndex readers 适配（PDF/Word/Excel/TXT） |
| `backend/app/ai/rag/indexer.py` | RAGIndexer：IngestionPipeline + chunking + 增量 |
| `backend/app/ai/rag/retriever.py` | RAGRetriever：hybrid(vector+BM25) + HttpReranker |
| `backend/app/ai/agents/__init__.py` | 对外高层 API：`run_chat_agent`/`run_interpretation_agent` |
| `backend/app/ai/agents/tools.py` | `make_tools()` 共享工具集 |
| `backend/app/ai/agents/chat_graph.py` | Chat Agent StateGraph |
| `backend/app/ai/agents/interp_graph.py` | Interpretation Agent StateGraph |
| `backend/reranker_service/pyproject.toml` | reranker 服务依赖 |
| `backend/reranker_service/main.py` | FastAPI reranker 服务 |
| `backend/tests/ai/__init__.py` | 测试包 |
| `backend/tests/ai/test_config.py` | config 工厂测试 |
| `backend/tests/ai/test_llm.py` | LLM 封装测试 |
| `backend/tests/ai/rag/__init__.py` | 测试包 |
| `backend/tests/ai/rag/test_store.py` | RAGStore 测试 |
| `backend/tests/ai/rag/test_readers.py` | readers 测试 |
| `backend/tests/ai/rag/test_indexer.py` | indexer 测试 |
| `backend/tests/ai/rag/test_retriever.py` | retriever 测试 |
| `backend/tests/ai/agents/__init__.py` | 测试包 |
| `backend/tests/ai/agents/test_tools.py` | 工具集测试 |
| `backend/tests/ai/agents/test_chat_graph.py` | chat 图测试 |
| `backend/tests/ai/agents/test_interp_graph.py` | interp 图测试 |
| `backend/scripts/reindex_existing.py` | 数据迁移脚本 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `backend/pyproject.toml` | 新增 9 个依赖 + pytest 测试依赖 |
| `backend/app/config.py` | 新增 reranker/RAG/Agent 配置字段 |
| `backend/app/main.py` | 启动时调 `ai.config` 初始化 milvus-lite |
| `backend/app/modules/knowledge/service.py` | 删手写 Milvus 调用，改调 `ai.rag` |
| `backend/app/modules/knowledge/internal.py` | search 改调 `ai.rag.search()` |
| `backend/app/modules/chat/service.py` | 删手写 prompt 拼装，改调 `ai.agents.run_chat_agent` |
| `backend/app/modules/chat/stream.py` | SSE 增加 `tool_status` 事件 |
| `backend/app/modules/interpretation/service.py` | 删 `process_interpretation` 主体，改调 `ai.agents.run_interpretation_agent` |
| `backend/app/modules/interpretation/worker.py` | 调 `run_interpretation_agent` |
| `start_local.sh` | 增加 reranker 服务启动 |
| `start.sh` | 同上 |
| `start_windows_local.bat` | 同上 |

### 删除文件

| 文件 | 原因 |
|------|------|
| `backend/app/core/milvus.py` | 功能搬入 `ai/rag/store.py` + `ai/config.py` |
| `backend/app/core/embedding.py` | 功能由 `ai/config.py` 工厂替代 |
| `backend/app/core/llm_client.py` | 功能由 `ai/llm.py` + Agent 图替代 |
| `backend/app/core/doc_parser.py` | 功能由 `ai/rag/readers.py` 替代 |

---

## Task 1: 依赖与配置基础

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/config.py`
- Create: `backend/tests/ai/__init__.py`

**Interfaces:**
- Produces: `settings.RERANKER_*`/`settings.RAG_*`/`settings.AGENT_MAX_ITERATIONS` 配置字段供后续任务读取

- [ ] **Step 1: 在 pyproject.toml 新增依赖**

编辑 `backend/pyproject.toml`，在 `dependencies` 数组里追加：

```toml
    "langchain-core>=0.3,<0.4",
    "langchain-openai>=0.2,<0.3",
    "langgraph>=0.2,<0.3",
    "llama-index-core>=0.12,<0.13",
    "llama-index-vector-stores-milvus>=0.4,<0.5",
    "llama-index-readers-file>=0.4,<0.5",
    "llama-index-embeddings-openai>=0.3,<0.4",
    "llama-index-postprocessor-flag-embedding-reranker>=0.3,<0.4",
    "llama-index-retrievers-bm25>=0.5,<0.6",
    "FlagEmbedding>=1.3,<2",
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
```

- [ ] **Step 2: 安装依赖**

Run: `cd backend && uv sync`
Expected: 成功安装所有新依赖，无冲突

- [ ] **Step 3: 在 config.py 新增配置字段**

编辑 `backend/app/config.py`，在 `Settings` 类的 `# LLM 通用` 字段块之后追加：

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

- [ ] **Step 4: 创建测试包目录**

Run: `mkdir -p backend/tests/ai/rag backend/tests/ai/agents`
创建空文件 `backend/tests/ai/__init__.py`、`backend/tests/ai/rag/__init__.py`、`backend/tests/ai/agents/__init__.py`

- [ ] **Step 5: 验证配置可加载**

Run: `cd backend && uv run python -c "from app.config import settings; print(settings.RAG_CHUNK_SIZE, settings.RERANKER_MODEL, settings.AGENT_MAX_ITERATIONS)"`
Expected: 输出 `512 BAAI/bge-reranker-v2-m3 8`

- [ ] **Step 6: Commit**

```bash
cd backend && git add pyproject.toml app/config.py tests/ai/__init__.py tests/ai/rag/__init__.py tests/ai/agents/__init__.py && git commit -m "feat: add LlamaIndex/LangGraph dependencies and RAG/Agent config fields"
```

---

## Task 2: ai/config.py — Provider 工厂与 milvus-lite 启动

**Files:**
- Create: `backend/app/ai/__init__.py`
- Create: `backend/app/ai/config.py`
- Test: `backend/tests/ai/test_config.py`

**Interfaces:**
- Produces: `get_embedding_model() -> BaseEmbedding`，`get_chat_model(streaming) -> ChatOpenAI`（注：`get_chat_model` 实现在 Task 3 的 `llm.py`，但 `config.py` 导入它），`ensure_milvus_started()`，`VECTOR_DIM: int`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/test_config.py`：

```python
from unittest.mock import patch, MagicMock


def test_vector_dim_bge_m3():
    """bge-m3 embedding provider 对应 1024 维"""
    with patch("app.config.settings.EMBED_PROVIDER", "local"):
        from app.ai.config import VECTOR_DIM
        assert VECTOR_DIM == 1024


def test_get_embedding_model_local():
    """local provider 返回 OpenAIEmbedding 指向 vLLM"""
    with patch("app.config.settings.EMBED_PROVIDER", "local"):
        from app.ai.config import get_embedding_model
        model = get_embedding_model()
        assert model is not None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai'`

- [ ] **Step 3: 创建 ai 包和 config.py**

创建 `backend/app/ai/__init__.py`（空文件）。

创建 `backend/app/ai/config.py`：

```python
from pymilvus import connections

from app.config import settings

VECTOR_DIM = 1024  # bge-m3 / text-embedding-v3 均为 1024

_milvus_started = False


def ensure_milvus_started():
    """启动 milvus-lite 嵌入式服务并建立连接（从旧 core/milvus.py 搬迁）"""
    global _milvus_started
    if _milvus_started:
        return
    from milvus_lite import server_manager
    server_manager.start(port=settings.MILVUS_PORT)
    connections.connect(host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
    _milvus_started = True


def get_embedding_model():
    """根据 EMBED_PROVIDER 构造 LlamaIndex Embedding 模型"""
    from llama_index.embeddings.openai import OpenAIEmbedding

    if settings.EMBED_PROVIDER == "remote":
        return OpenAIEmbedding(
            api_base=settings.REMOTE_EMBED_BASE_URL,
            api_key=settings.REMOTE_EMBED_API_KEY,
            model_name=settings.REMOTE_EMBED_MODEL,
            embed_dim=VECTOR_DIM,
        )
    return OpenAIEmbedding(
        api_base=settings.EMBED_BASE_URL,
        api_key="not-required",
        model_name=settings.EMBED_MODEL_NAME,
        embed_dim=VECTOR_DIM,
    )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/__init__.py app/ai/config.py tests/ai/test_config.py && git commit -m "feat: add ai/config.py with embedding factory and milvus-lite startup"
```

---

## Task 3: ai/llm.py — LangChain ChatModel 封装

**Files:**
- Create: `backend/app/ai/llm.py`
- Test: `backend/tests/ai/test_llm.py`

**Interfaces:**
- Produces: `get_chat_model(streaming: bool = False) -> ChatOpenAI`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/test_llm.py`：

```python
from unittest.mock import patch


def test_get_chat_model_local():
    """local provider 返回 ChatOpenAI 指向 vLLM"""
    with patch("app.config.settings.LLM_PROVIDER", "local"):
        from app.ai.llm import get_chat_model
        model = get_chat_model()
        assert model.model_name == "qwen2.5" or model.model == "qwen2.5"


def test_get_chat_model_remote():
    """remote provider 返回 ChatOpenAI 指向远端 API"""
    with patch("app.config.settings.LLM_PROVIDER", "remote"):
        from app.ai.llm import get_chat_model
        model = get_chat_model(streaming=True)
        assert model.streaming is True
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai.llm'`

- [ ] **Step 3: 创建 llm.py**

创建 `backend/app/ai/llm.py`：

```python
from langchain_openai import ChatOpenAI

from app.config import settings


def get_chat_model(streaming: bool = False) -> ChatOpenAI:
    """根据 LLM_PROVIDER 构造 LangChain ChatOpenAI，兼容 vLLM/远端"""
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

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/test_llm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/llm.py tests/ai/test_llm.py && git commit -m "feat: add ai/llm.py LangChain ChatOpenAI wrapper"
```

---

## Task 4: ai/rag/store.py — RAGStore

**Files:**
- Create: `backend/app/ai/rag/__init__.py`（空占位，Task 9 填充高层 API）
- Create: `backend/app/ai/rag/store.py`
- Test: `backend/tests/ai/rag/test_store.py`

**Interfaces:**
- Produces: `rag_store: RAGStore`，方法 `get(hospital_id) -> MilvusVectorStore`、`get_index(hospital_id) -> VectorStoreIndex`、`get_nodes(hospital_id) -> list`、`refresh(hospital_id)`、`drop(hospital_id)`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/rag/test_store.py`：

```python
from unittest.mock import patch, MagicMock


def test_rag_store_caches_per_hospital():
    """同一 hospital_id 的 MilvusVectorStore 只创建一次"""
    with patch("app.ai.config.ensure_milvus_started"):
        from app.ai.rag.store import RAGStore
        store = RAGStore()
        with patch("app.ai.rag.store.MilvusVectorStore") as MockVS:
            mock_inst = MagicMock()
            MockVS.return_value = mock_inst
            s1 = store.get("H001")
            s2 = store.get("H001")
            assert s1 is s2
            MockVS.assert_called_once()
            s3 = store.get("H002")
            assert s3 is not s1
            assert MockVS.call_count == 2


def test_rag_store_refresh_clears_cache():
    """refresh 后下次 get 重建"""
    with patch("app.ai.config.ensure_milvus_started"):
        from app.ai.rag.store import RAGStore
        store = RAGStore()
        with patch("app.ai.rag.store.MilvusVectorStore") as MockVS:
            MockVS.return_value = MagicMock()
            store.get("H001")
            assert "H001" in store._stores
            store.refresh("H001")
            assert "H001" not in store._indices
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/rag/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 创建 store.py**

创建 `backend/app/ai/rag/__init__.py`（空文件）。

创建 `backend/app/ai/rag/store.py`：

```python
from typing import Optional

from app.ai.config import ensure_milvus_started, get_embedding_model, VECTOR_DIM
from app.config import settings


class RAGStore:
    """按医院隔离的 LlamaIndex MilvusVectorStore 单例工厂"""

    def __init__(self):
        self._stores: dict[str, "MilvusVectorStore"] = {}
        self._indices: dict[str, "VectorStoreIndex"] = {}
        self._nodes_cache: dict[str, list] = {}

    def get(self, hospital_id: str):
        """获取或创建某医院的 MilvusVectorStore"""
        ensure_milvus_started()
        if hospital_id not in self._stores:
            from llama_index.vector_stores.milvus import MilvusVectorStore

            self._stores[hospital_id] = MilvusVectorStore(
                uri=f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
                collection_name=f"hospital_{hospital_id}_knowledge",
                dim=VECTOR_DIM,
                overwrite=False,
                metric_type="IP",
                index_config={
                    "index_type": "IVF_FLAT",
                    "params": {"nlist": 128},
                },
            )
        return self._stores[hospital_id]

    def get_index(self, hospital_id: str):
        """构造 VectorStoreIndex 并缓存，供 VectorIndexRetriever 使用"""
        if hospital_id not in self._indices:
            from llama_index.core import VectorStoreIndex

            self._indices[hospital_id] = VectorStoreIndex(
                vector_store=self.get(hospital_id),
                embed_model=get_embedding_model(),
            )
        return self._indices[hospital_id]

    def get_nodes(self, hospital_id: str) -> list:
        """拉取所有节点供 BM25Retriever 构建"""
        if hospital_id not in self._nodes_cache:
            index = self.get_index(hospital_id)
            self._nodes_cache[hospital_id] = list(index.docstore.docs.values())
        return self._nodes_cache[hospital_id]

    def refresh(self, hospital_id: str):
        """知识库更新后清缓存，下次 get_* 重建"""
        self._indices.pop(hospital_id, None)
        self._nodes_cache.pop(hospital_id, None)

    def drop(self, hospital_id: str):
        """reindex 用：drop collection 并清缓存"""
        from pymilvus import utility

        ensure_milvus_started()
        collection_name = f"hospital_{hospital_id}_knowledge"
        if utility.has_collection(collection_name):
            utility.drop_collection(collection_name)
        self._stores.pop(hospital_id, None)
        self.refresh(hospital_id)


rag_store = RAGStore()
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/rag/test_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/rag/__init__.py app/ai/rag/store.py tests/ai/rag/test_store.py && git commit -m "feat: add ai/rag/store.py RAGStore with per-hospital MilvusVectorStore"
```

---

## Task 5: ai/rag/readers.py — 文档解析适配

**Files:**
- Create: `backend/app/ai/rag/readers.py`
- Test: `backend/tests/ai/rag/test_readers.py`

**Interfaces:**
- Produces: `load_documents(file_path: str, filename: str) -> list[Document]`（LlamaIndex `Document`）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/rag/test_readers.py`：

```python
import os
import tempfile

from app.ai.rag.readers import load_documents


def test_load_txt_document():
    """txt 文件解析为 LlamaIndex Document"""
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as f:
        f.write("这是一段测试文本。\n第二段落内容。")
        path = f.name
    try:
        docs = load_documents(path, "test.txt")
        assert len(docs) >= 1
        assert "测试文本" in docs[0].text
        assert docs[0].metadata.get("source_file") == "test.txt"
    finally:
        os.unlink(path)


def test_load_unsupported_format_raises():
    """不支持的格式抛出 ValueError"""
    import pytest
    with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
        path = f.name
    try:
        with pytest.raises(ValueError, match="Unsupported"):
            load_documents(path, "test.xyz")
    finally:
        os.unlink(path)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/rag/test_readers.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 创建 readers.py**

创建 `backend/app/ai/rag/readers.py`：

```python
from pathlib import Path
from typing import List

from llama_index.core import Document


def load_documents(file_path: str, filename: str) -> List[Document]:
    """按扩展名路由到 LlamaIndex reader，返回带 metadata 的 Document 列表"""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _read_pdf(file_path, filename)
    elif ext in (".docx", ".doc"):
        return _read_docx(file_path, filename)
    elif ext in (".xlsx", ".xls"):
        return _read_excel(file_path, filename)
    elif ext in (".txt", ".md"):
        return _read_text(file_path, filename)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _read_pdf(file_path: str, filename: str) -> List[Document]:
    from llama_index.readers.file import PyMuPDFReader

    reader = PyMuPDFReader()
    docs = reader.load(file_path=file_path)
    for d in docs:
        d.metadata["source_file"] = filename
        d.metadata["file_ext"] = ".pdf"
    return docs


def _read_docx(file_path: str, filename: str) -> List[Document]:
    from llama_index.readers.file import DocxReader

    reader = DocxReader()
    docs = reader.load(file_path=file_path)
    for d in docs:
        d.metadata["source_file"] = filename
        d.metadata["file_ext"] = ".docx"
    return docs


def _read_excel(file_path: str, filename: str) -> List[Document]:
    from llama_index.readers.file import PandasExcelReader

    reader = PandasExcelReader(sheet_name=None)
    docs = reader.load(file_path=file_path)
    for d in docs:
        d.metadata["source_file"] = filename
        d.metadata["file_ext"] = ".xlsx"
    return docs


def _read_text(file_path: str, filename: str) -> List[Document]:
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()
    if not text.strip():
        return []
    return [Document(
        text=text,
        metadata={"source_file": filename, "file_ext": Path(filename).suffix.lower()},
    )]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/rag/test_readers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/rag/readers.py tests/ai/rag/test_readers.py && git commit -m "feat: add ai/rag/readers.py LlamaIndex document readers adapter"
```

---

## Task 6: ai/rag/indexer.py — IngestionPipeline

**Files:**
- Create: `backend/app/ai/rag/indexer.py`
- Test: `backend/tests/ai/rag/test_indexer.py`

**Interfaces:**
- Produces: `RAGIndexer` 类，方法 `index_documents(docs, category_id, source_file) -> list[str]`、`delete_by_entry(entry_id)`、`reindex_all(entries)`
- Consumes: `rag_store`（Task 4）、`get_embedding_model()`（Task 2）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/rag/test_indexer.py`：

```python
from unittest.mock import patch, MagicMock

from llama_index.core import Document


def test_index_documents_calls_pipeline_run():
    """index_documents 注入 metadata 后调 pipeline.run"""
    with patch("app.ai.rag.indexer.rag_store") as mock_store, \
         patch("app.ai.rag.indexer.get_embedding_model") as mock_embed, \
         patch("app.ai.rag.indexer.IngestionPipeline") as MockPipeline:
        mock_pipeline = MagicMock()
        MockPipeline.return_value = mock_pipeline
        mock_node = MagicMock()
        mock_node.node_id = "node-1"
        mock_pipeline.run.return_value = [mock_node]

        from app.ai.rag.indexer import RAGIndexer
        indexer = RAGIndexer("H001")
        docs = [Document(text="测试内容", metadata={})]
        ids = indexer.index_documents(docs, category_id=5, source_file="test.pdf")

        assert ids == ["node-1"]
        assert docs[0].metadata["category_id"] == 5
        assert docs[0].metadata["source_file"] == "test.pdf"
        assert docs[0].metadata["hospital_id"] == "H001"
        mock_store.refresh.assert_called_once_with("H001")


def test_reindex_all_drops_and_rebuilds():
    """reindex_all 调 rag_store.drop 并逐条 ingest"""
    with patch("app.ai.rag.indexer.rag_store") as mock_store, \
         patch("app.ai.rag.indexer.get_embedding_model"), \
         patch("app.ai.rag.indexer.IngestionPipeline") as MockPipeline:
        mock_pipeline = MagicMock()
        MockPipeline.return_value = mock_pipeline

        from app.ai.rag.indexer import RAGIndexer
        indexer = RAGIndexer("H001")
        entries = [
            {"id": 1, "title": "条目1", "content": "内容1", "category_id": 2, "source_file": "a.pdf"},
            {"id": 2, "title": "条目2", "content": "内容2", "category_id": 3, "source_file": "b.pdf"},
        ]
        indexer.reindex_all(entries)

        mock_store.drop.assert_called_once_with("H001")
        assert mock_pipeline.run.call_count == 2
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/rag/test_indexer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 创建 indexer.py**

创建 `backend/app/ai/rag/indexer.py`：

```python
from typing import Optional

from llama_index.core import Document
from llama_index.core.ingestion import IngestionPipeline, IngestionCache
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.storage.docstore import SimpleKVStore

from app.ai.config import get_embedding_model
from app.ai.rag.store import rag_store
from app.config import settings


class RAGIndexer:
    """文档→chunk→embed→Milvus 的 LlamaIndex IngestionPipeline 封装"""

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

    def index_documents(
        self,
        docs: list[Document],
        category_id: Optional[int],
        source_file: str,
    ) -> list[str]:
        """批量入库，返回 node_ids"""
        for d in docs:
            d.metadata.update({
                "category_id": category_id or 0,
                "source_file": source_file,
                "hospital_id": self.hospital_id,
            })
        nodes = self.pipeline.run(documents=docs)
        rag_store.refresh(self.hospital_id)
        return [n.node_id for n in nodes]

    def delete_by_entry(self, entry_id: int):
        """按 entry_id 删 Milvus 向量"""
        rag_store.get(self.hospital_id).delete(
            filter={"entry_id": entry_id}
        )
        rag_store.refresh(self.hospital_id)

    def reindex_all(self, entries: list[dict]):
        """全量重建：drop collection → 逐条 ingest"""
        rag_store.drop(self.hospital_id)
        self._docstores[self.hospital_id] = SimpleKVStore()
        self._caches[self.hospital_id] = IngestionCache()
        self.pipeline.docstore = self._docstores[self.hospital_id]
        self.pipeline.cache = self._caches[self.hospital_id]
        for e in entries:
            docs = [Document(
                text=e["content"],
                metadata={
                    "entry_id": e["id"],
                    "category_id": e.get("category_id") or 0,
                    "title": e["title"],
                    "source_file": e.get("source_file") or "",
                },
            )]
            self.pipeline.run(documents=docs)
        rag_store.refresh(self.hospital_id)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/rag/test_indexer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/rag/indexer.py tests/ai/rag/test_indexer.py && git commit -m "feat: add ai/rag/indexer.py IngestionPipeline with incremental dedup"
```

---

## Task 7: reranker_service — 独立 Reranker HTTP 服务

**Files:**
- Create: `backend/reranker_service/pyproject.toml`
- Create: `backend/reranker_service/main.py`

**Interfaces:**
- Produces: HTTP 服务 `POST /rerank`，入参 `{"query": str, "documents": list[str], "top_n": int}`，出参 `{"results": [{"index": int, "score": float, "document": str}]}`

- [ ] **Step 1: 创建服务目录和 pyproject.toml**

创建 `backend/reranker_service/pyproject.toml`：

```toml
[project]
name = "reranker-service"
version = "0.1.0"
description = "Standalone BGE reranker HTTP service"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "FlagEmbedding>=1.3,<2",
    "pydantic>=2.0",
]
```

- [ ] **Step 2: 创建 main.py**

创建 `backend/reranker_service/main.py`：

```python
import os
from typing import List

from fastapi import FastAPI
from pydantic import BaseModel, Field

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

app = FastAPI(title="Reranker Service")

_model = None


def _get_model():
    global _model
    if _model is None:
        from FlagEmbedding import FlagReranker
        model_name = os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
        _model = FlagReranker(model_name, use_fp16=True)
    return _model


class RerankRequest(BaseModel):
    query: str
    documents: List[str]
    top_n: int = Field(default=5, ge=1)


class RerankResult(BaseModel):
    index: int
    score: float
    document: str


class RerankResponse(BaseModel):
    results: List[RerankResult]


@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    model = _get_model()
    pairs = [[req.query, doc] for doc in req.documents]
    scores = model.compute_score(pairs, normalize=True)
    if not isinstance(scores, list):
        scores = [scores]
    ranked = sorted(
        enumerate(scores), key=lambda x: x[1], reverse=True
    )[:req.top_n]
    return RerankResponse(results=[
        RerankResult(index=i, score=float(s), document=req.documents[i])
        for i, s in ranked
    ])


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 3: 验证服务可启动（语法检查）**

Run: `cd backend/reranker_service && uv run python -c "import ast; ast.parse(open('main.py').read()); print('syntax ok')"`
Expected: `syntax ok`

注：完整启动测试需要下载模型，在集成阶段验证。

- [ ] **Step 4: Commit**

```bash
cd backend && git add reranker_service/ && git commit -m "feat: add standalone BGE reranker HTTP service"
```

---

## Task 8: ai/rag/retriever.py — Hybrid 检索 + Reranker

**Files:**
- Create: `backend/app/ai/rag/retriever.py`
- Test: `backend/tests/ai/rag/test_retriever.py`

**Interfaces:**
- Produces: `RAGRetriever` 类，方法 `retrieve(query, category_ids, top_k) -> list[SearchResult]`；`HttpReranker` 类（`BaseNodePostprocessor` 子类）
- Consumes: `rag_store`（Task 4）、`settings.RAG_*`（Task 1）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/rag/test_retriever.py`：

```python
from unittest.mock import patch, MagicMock


def test_retrieve_with_reranker_downgrade():
    """reranker 不可用时降级返回 fusion 结果"""
    with patch("app.ai.rag.retriever.rag_store") as mock_store, \
         patch("app.ai.rag.retriever.get_embedding_model"):
        mock_index = MagicMock()
        mock_store.get_index.return_value = mock_index
        mock_store.get_nodes.return_value = []

        from app.ai.rag.retriever import RAGRetriever
        retriever = RAGRetriever("H001")

        with patch.object(retriever._fusion, "retrieve") as mock_fusion, \
             patch.object(retriever._reranker, "postprocess_nodes", side_effect=Exception("conn refused")):
            mock_node = MagicMock()
            mock_node.text = "内容"
            mock_node.metadata = {"entry_id": 1, "title": "标题", "category_id": 2}
            mock_node.score = 0.9
            mock_fusion.return_value = [mock_node]

            results = retriever.retrieve("查询")
            assert len(results) == 1
            assert results[0].entry_id == 1
            assert results[0].content == "内容"


def test_retrieve_empty_results():
    """无结果时返回空列表"""
    with patch("app.ai.rag.retriever.rag_store") as mock_store, \
         patch("app.ai.rag.retriever.get_embedding_model"):
        mock_store.get_index.return_value = MagicMock()
        mock_store.get_nodes.return_value = []

        from app.ai.rag.retriever import RAGRetriever
        retriever = RAGRetriever("H001")

        with patch.object(retriever._fusion, "retrieve", return_value=[]):
            results = retriever.retrieve("查询")
            assert results == []
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/rag/test_retriever.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 创建 retriever.py**

创建 `backend/app/ai/rag/retriever.py`：

```python
import os
from typing import List, Optional

import httpx
from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever

from app.ai.config import get_embedding_model
from app.ai.rag.store import rag_store
from app.config import settings
from app.modules.knowledge.schemas import SearchResult


class HttpReranker(BaseNodePostprocessor):
    """调外部 reranker HTTP 服务的 postprocessor"""

    def __init__(self, top_n: int = 5, base_url: str = "", model: str = ""):
        self.top_n = top_n
        self.base_url = base_url or settings.RERANKER_BASE_URL
        self.model = model or settings.RERANKER_MODEL
        self._client = httpx.Client(timeout=30.0)

    def _postprocess_nodes(
        self, nodes: List[NodeWithScore], query_str: str = None
    ) -> List[NodeWithScore]:
        if not nodes:
            return nodes
        documents = [n.node.text for n in nodes]
        try:
            resp = self._client.post(
                f"{self.base_url}/rerank",
                json={"query": query_str, "documents": documents, "top_n": self.top_n},
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            ranked = []
            for r in results:
                idx = r["index"]
                if idx < len(nodes):
                    node = nodes[idx]
                    node.score = r["score"]
                    ranked.append(node)
            return ranked[:self.top_n]
        except Exception:
            return nodes[:self.top_n]


class RAGRetriever:
    """向量 + BM25 融合检索 → reranker 重排 → 返回结构化结果"""

    def __init__(self, hospital_id: str):
        self.hospital_id = hospital_id
        index = rag_store.get_index(hospital_id)

        self._vector_retriever = index.as_retriever(
            similarity_top_k=settings.RAG_VECTOR_TOP_K,
        )

        nodes = rag_store.get_nodes(hospital_id)
        if nodes:
            self._bm25_retriever = BM25Retriever.from_nodes(
                nodes, similarity_top_k=settings.RAG_VECTOR_TOP_K
            )
        else:
            self._bm25_retriever = None

        retrievers = [self._vector_retriever]
        if self._bm25_retriever:
            retrievers.append(self._bm25_retriever)

        self._fusion = QueryFusionRetriever(
            retrievers,
            similarity_top_k=settings.RAG_VECTOR_TOP_K,
            mode="reciprocal_rerank",
        )
        self._reranker = HttpReranker(top_n=settings.RAG_FINAL_TOP_K)

    def retrieve(
        self,
        query: str,
        category_ids: Optional[List[int]] = None,
        top_k: Optional[int] = None,
    ) -> List[SearchResult]:
        """检索并返回 SearchResult 列表"""
        from llama_index.core.vector_stores import MetadataFilters, MetadataFilter

        filters = None
        if category_ids:
            filters = MetadataFilters(filters=[
                MetadataFilter(key="category_id", value=category_ids, operator="IN")
            ])

        try:
            nodes = self._fusion.retrieve(query, filters=filters)
        except Exception:
            try:
                nodes = self._vector_retriever.retrieve(query, filters=filters)
            except Exception:
                return []

        try:
            nodes = self._reranker.postprocess_nodes(nodes, query_str=query)
        except Exception:
            pass

        if top_k:
            nodes = nodes[:top_k]

        out = []
        for n in nodes:
            out.append(SearchResult(
                entry_id=n.metadata.get("entry_id", 0),
                title=n.metadata.get("title", ""),
                content=n.node.text,
                category_id=n.metadata.get("category_id"),
                score=float(n.score or 0),
            ))
        return out
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/rag/test_retriever.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/rag/retriever.py tests/ai/rag/test_retriever.py && git commit -m "feat: add ai/rag/retriever.py hybrid retrieval with HttpReranker"
```

---

## Task 9: ai/rag/__init__.py — 对外高层 API

**Files:**
- Modify: `backend/app/ai/rag/__init__.py`（Task 4 创建的空文件）
- Test: `backend/tests/ai/rag/test_api.py`

**Interfaces:**
- Produces: `index_documents(hospital_id, docs, category_id, source_file) -> list[str]`、`delete_vectors(hospital_id, entry_id)`、`search(hospital_id, query, category_ids, top_k) -> list[SearchResult]`、`reindex_hospital(hospital_id, entries)`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/rag/test_api.py`：

```python
from unittest.mock import patch, MagicMock

from llama_index.core import Document


def test_search_delegates_to_retriever():
    with patch("app.ai.rag.RAGRetriever") as MockRetriever:
        mock_inst = MagicMock()
        MockRetriever.return_value = mock_inst
        from app.modules.knowledge.schemas import SearchResult
        mock_inst.retrieve.return_value = [SearchResult(
            entry_id=1, title="t", content="c", category_id=2, score=0.9
        )]

        from app.ai.rag import search
        results = search("H001", "query")
        assert len(results) == 1
        assert results[0].entry_id == 1


def test_index_documents_delegates_to_indexer():
    with patch("app.ai.rag.RAGIndexer") as MockIndexer:
        mock_inst = MagicMock()
        MockIndexer.return_value = mock_inst
        mock_inst.index_documents.return_value = ["n1"]

        from app.ai.rag import index_documents
        ids = index_documents("H001", [Document(text="x")], 1, "f.pdf")
        assert ids == ["n1"]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/rag/test_api.py -v`
Expected: FAIL — 函数未定义

- [ ] **Step 3: 填充 __init__.py**

编辑 `backend/app/ai/rag/__init__.py`：

```python
from typing import List, Optional

from llama_index.core import Document

from app.ai.rag.indexer import RAGIndexer
from app.ai.rag.retriever import RAGRetriever
from app.modules.knowledge.schemas import SearchResult


def index_documents(
    hospital_id: str,
    docs: List[Document],
    category_id: Optional[int],
    source_file: str,
) -> List[str]:
    """入库文档到指定医院的向量库"""
    indexer = RAGIndexer(hospital_id)
    return indexer.index_documents(docs, category_id, source_file)


def delete_vectors(hospital_id: str, entry_id: int) -> None:
    """删除指定条目的向量"""
    indexer = RAGIndexer(hospital_id)
    indexer.delete_by_entry(entry_id)


def search(
    hospital_id: str,
    query: str,
    category_ids: Optional[List[int]] = None,
    top_k: Optional[int] = None,
) -> List[SearchResult]:
    """混合检索 + rerank"""
    retriever = RAGRetriever(hospital_id)
    return retriever.retrieve(query, category_ids=category_ids, top_k=top_k)


def reindex_hospital(hospital_id: str, entries: List[dict]) -> None:
    """全量重建某医院知识库向量"""
    indexer = RAGIndexer(hospital_id)
    indexer.reindex_all(entries)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/rag/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/rag/__init__.py tests/ai/rag/test_api.py && git commit -m "feat: add ai/rag high-level API (index/search/delete/reindex)"
```

---

## Task 10: 迁移 knowledge/service.py + internal.py

**Files:**
- Modify: `backend/app/modules/knowledge/service.py`
- Modify: `backend/app/modules/knowledge/internal.py`
- Test: `backend/tests/ai/rag/test_knowledge_migration.py`

**Interfaces:**
- Consumes: `ai.rag.index_documents`/`delete_vectors`/`search`/`reindex_hospital`（Task 9）
- Produces: `knowledge/service.py` 的 CRUD 不再调手写 Milvus，改调 `ai.rag`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/rag/test_knowledge_migration.py`：

```python
from unittest.mock import patch, MagicMock


def test_create_entry_calls_rag_index():
    """create_entry 后调 ai.rag.index_documents"""
    with patch("app.modules.knowledge.service.get_hospital_db") as mock_db_fn, \
         patch("app.modules.knowledge.service.ai.rag") as mock_rag:
        mock_db = MagicMock()
        mock_db_fn.return_value = iter([mock_db])
        mock_entry = MagicMock()
        mock_entry.id = 1
        mock_entry.content = "内容"
        mock_entry.title = "标题"
        mock_entry.category_id = None
        mock_entry.source_file = None
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()
        mock_db.refresh = MagicMock(side_effect=lambda x: setattr(x, "id", 1))

        from app.modules.knowledge import service
        service.create_entry(mock_db, "H001", "标题", "内容", None)
        mock_rag.index_documents.assert_called_once()


def test_search_delegates_to_ai_rag():
    """knowledge search 调 ai.rag.search"""
    with patch("app.modules.knowledge.service.ai.rag") as mock_rag:
        from app.modules.knowledge.schemas import SearchResult
        mock_rag.search.return_value = [SearchResult(
            entry_id=1, title="t", content="c", category_id=None, score=0.9
        )]
        from app.modules.knowledge import service
        results = service.search("H001", "query")
        assert len(results) == 1
        mock_rag.search.assert_called_once()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/rag/test_knowledge_migration.py -v`
Expected: FAIL — `service.py` 还没改

- [ ] **Step 3: 改造 knowledge/service.py**

编辑 `backend/app/modules/knowledge/service.py`。

替换文件开头的 import 区块（第 1-8 行）为：

```python
from typing import List, Optional
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.modules.knowledge.models import KnowledgeCategory, KnowledgeEntry
from app.modules.knowledge.schemas import SearchResult
from app.ai import rag as ai_rag
from llama_index.core import Document
```

删除 `_vectorize_entry` 函数（第 120-131 行）。

删除 `search` 函数（第 168-197 行），替换为：

```python
def search(hospital_id: str, query: str, top_k: int = 5,
           category_ids: Optional[List[int]] = None) -> List[SearchResult]:
    return ai_rag.search(hospital_id, query, category_ids=category_ids, top_k=top_k)
```

删除 `reindex_category` 函数（第 200-201 行），替换为：

```python
def reindex_category(hospital_id: str, category_id: int):
    """全量重建某分类的向量（实际重建整个医院，因 Milvus collection 按医院隔离）"""
    db = next(get_hospital_db(hospital_id))
    try:
        entries = db.query(KnowledgeEntry).filter(
            KnowledgeEntry.status == 1,
            KnowledgeEntry.category_id == category_id,
        ).all()
        entry_dicts = [
            {"id": e.id, "title": e.title, "content": e.content,
             "category_id": e.category_id, "source_file": e.source_file}
            for e in entries
        ]
    finally:
        db.close()
    if entry_dicts:
        ai_rag.reindex_hospital(hospital_id, entry_dicts)
```

改造 `create_entry`（第 76-84 行）：

```python
def create_entry(db: Session, hospital_id: str, title: str, content: str,
                 category_id: Optional[int] = None) -> KnowledgeEntry:
    entry = KnowledgeEntry(category_id=category_id, title=title, content=content, source_type="manual")
    db.add(entry)
    db.commit()
    db.refresh(entry)
    ai_rag.index_documents(hospital_id, [Document(text=content, metadata={
        "entry_id": entry.id, "title": title,
    })], category_id, "manual")
    return entry
```

改造 `update_entry`（第 87-106 行）：

```python
def update_entry(db: Session, hospital_id: str, entry_id: int,
                 title: Optional[str] = None, content: Optional[str] = None,
                 category_id: Optional[int] = None) -> Optional[KnowledgeEntry]:
    entry = get_entry(db, entry_id)
    if not entry:
        return None
    if category_id is not None:
        entry.category_id = category_id
    if title is not None:
        entry.title = title
    if content is not None:
        entry.content = content
        db.commit()
        ai_rag.delete_vectors(hospital_id, entry.id)
        ai_rag.index_documents(hospital_id, [Document(text=content, metadata={
            "entry_id": entry.id, "title": entry.title,
        })], entry.category_id, entry.source_file or "manual")
    db.commit()
    db.refresh(entry)
    return entry
```

改造 `delete_entry`（第 109-117 行）：

```python
def delete_entry(db: Session, hospital_id: str, entry_id: int) -> bool:
    entry = get_entry(db, entry_id)
    if not entry:
        return False
    entry.status = 0
    db.commit()
    ai_rag.delete_vectors(hospital_id, entry_id)
    return True
```

改造 `import_from_file`（第 136-163 行）：

```python
def import_from_file(db: Session, hospital_id: str, file_path: str,
                     filename: str, category_id: Optional[int] = None) -> int:
    from app.ai.rag.readers import load_documents
    docs = load_documents(file_path, filename)
    if not docs:
        return 0

    for doc in docs:
        entry = KnowledgeEntry(
            category_id=category_id, title=filename,
            content=doc.text, source_type="import", source_file=filename,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        doc.metadata["entry_id"] = entry.id

    ai_rag.index_documents(hospital_id, docs, category_id, filename)
    return len(docs)
```

- [ ] **Step 4: 改造 knowledge/internal.py**

编辑 `backend/app/modules/knowledge/internal.py`，替换为：

```python
from fastapi import APIRouter

from app.modules.knowledge import schemas, service

router = APIRouter()


@router.post("/search", response_model=schemas.SearchResponse)
def search_knowledge(req: schemas.SearchRequest):
    results = service.search(
        hospital_id=req.hospital_id,
        query=req.query,
        top_k=req.top_k,
        category_ids=req.category_ids,
    )
    return schemas.SearchResponse(results=results)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/rag/test_knowledge_migration.py -v`
Expected: PASS

- [ ] **Step 6: 验证 knowledge 模块可导入**

Run: `cd backend && uv run python -c "from app.modules.knowledge import service; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/modules/knowledge/service.py app/modules/knowledge/internal.py tests/ai/rag/test_knowledge_migration.py && git commit -m "refactor: migrate knowledge module to use ai.rag (LlamaIndex)"
```

---

## Task 11: ai/agents/tools.py — 共享工具集

**Files:**
- Create: `backend/app/ai/agents/__init__.py`（空占位）
- Create: `backend/app/ai/agents/tools.py`
- Test: `backend/tests/ai/agents/test_tools.py`

**Interfaces:**
- Produces: `make_tools(hospital_id: str, db_session: Session) -> list[BaseTool]`，包含 `search_knowledge`/`get_report_indicators`/`get_report_summary`/`get_user_history_reports`/`get_indicator_history`/`get_triage_rules`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/agents/test_tools.py`：

```python
from unittest.mock import patch, MagicMock
from sqlalchemy.orm import Session


def test_make_tools_returns_six_tools():
    """make_tools 返回 6 个工具"""
    with patch("app.ai.agents.tools.ai_rag"):
        from app.ai.agents.tools import make_tools
        db = MagicMock(spec=Session)
        tools = make_tools("H001", db)
        assert len(tools) == 6
        names = {t.name for t in tools}
        assert names == {
            "search_knowledge", "get_report_indicators", "get_report_summary",
            "get_user_history_reports", "get_indicator_history", "get_triage_rules",
        }


def test_search_knowledge_tool_calls_rag():
    """search_knowledge 工具调 ai.rag.search"""
    with patch("app.ai.agents.tools.ai_rag") as mock_rag:
        from app.modules.knowledge.schemas import SearchResult
        mock_rag.search.return_value = [SearchResult(
            entry_id=1, title="t", content="c", category_id=2, score=0.9
        )]
        from app.ai.agents.tools import make_tools
        tools = make_tools("H001", MagicMock(spec=Session))
        search_tool = next(t for t in tools if t.name == "search_knowledge")
        result = search_tool.invoke({"query": "血糖"})
        assert isinstance(result, list)
        assert result[0]["entry_id"] == 1
        mock_rag.search.assert_called_once_with("H001", "血糖", None, None)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/agents/test_tools.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 创建 tools.py**

创建 `backend/app/ai/agents/__init__.py`（空文件）。

创建 `backend/app/ai/agents/tools.py`：

```python
from typing import List, Optional

from langchain_core.tools import tool, BaseTool
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai import rag as ai_rag


def make_tools(hospital_id: str, db_session: Session) -> List[BaseTool]:
    """构造绑定 hospital_id 和 db_session 的工具集，供 chat/interp Agent 共享"""

    @tool
    def search_knowledge(
        query: str,
        category_ids: Optional[List[int]] = None,
        top_k: Optional[int] = None,
    ) -> list[dict]:
        """搜索医学知识库，返回相关知识条目。用于查找指标解读、疾病知识、健康建议等医学信息。
        Args:
            query: 搜索查询，如"空腹血糖偏高"或"ALT 升高原因"
            category_ids: 可选，限定知识分类 ID 列表
            top_k: 可选，返回条数上限
        Returns:
            知识条目列表，每项含 entry_id/title/content/score
        """
        results = ai_rag.search(hospital_id, query, category_ids=category_ids, top_k=top_k)
        return [{"entry_id": r.entry_id, "title": r.title, "content": r.content, "score": r.score} for r in results]

    @tool
    def get_report_indicators(report_id: int) -> list[dict]:
        """获取体检报告的所有结构化指标数据。
        Args:
            report_id: 报告 ID
        Returns:
            指标列表，每项含 item_name/result_value/unit/ref_range_low/ref_range_high
        """
        rows = db_session.execute(
            text("SELECT id, item_name, item_name_standard, result_value, unit, "
                 "ref_range_low, ref_range_high FROM report_indicator WHERE report_id = :rid ORDER BY id"),
            {"rid": report_id},
        ).fetchall()
        return [{"id": r[0], "item_name": r[1], "item_name_standard": r[2],
                 "result_value": r[3], "unit": r[4],
                 "ref_range_low": r[5], "ref_range_high": r[6]} for r in rows]

    @tool
    def get_report_summary(report_id: int) -> dict:
        """获取报告概览信息（报告日期、整体判定、红黄绿计数）。
        Args:
            report_id: 报告 ID
        Returns:
            含 report_date/overall_level/red_count/yellow_count/green_count 的 dict
        """
        row = db_session.execute(
            text("SELECT r.report_date, r.name, i.overall_level, i.red_count, i.yellow_count, i.green_count "
                 "FROM report_info r LEFT JOIN report_interpretation i ON i.report_id = r.id "
                 "WHERE r.id = :rid"),
            {"rid": report_id},
        ).fetchone()
        if not row:
            return {}
        return {"report_date": str(row[0]) if row[0] else None, "name": row[1],
                "overall_level": row[2], "red_count": row[3],
                "yellow_count": row[4], "green_count": row[5]}

    @tool
    def get_user_history_reports(user_id: int, limit: int = 5) -> list[dict]:
        """获取用户历年体检报告概览，用于趋势对比。
        Args:
            user_id: 用户 ID
            limit: 返回条数，默认 5
        Returns:
            报告列表，每项含 report_id/report_date/overall_level
        """
        rows = db_session.execute(
            text("SELECT r.id, r.report_date, i.overall_level "
                 "FROM report_info r LEFT JOIN report_interpretation i ON i.report_id = r.id "
                 "WHERE r.user_id = :uid ORDER BY r.report_date DESC LIMIT :lim"),
            {"uid": user_id, "lim": limit},
        ).fetchall()
        return [{"report_id": r[0], "report_date": str(r[1]) if r[1] else None,
                 "overall_level": r[2]} for r in rows]

    @tool
    def get_indicator_history(user_id: int, item_name: str) -> list[dict]:
        """获取用户某指标的历史数值，用于趋势研判。
        Args:
            user_id: 用户 ID
            item_name: 指标名称
        Returns:
            历史数值列表，每项含 date/value/unit
        """
        rows = db_session.execute(
            text("SELECT ri.report_date, ind.result_value, ind.unit "
                 "FROM report_indicator ind "
                 "JOIN report_info ri ON ind.report_id = ri.id "
                 "WHERE ri.user_id = :uid AND ind.item_name = :name "
                 "ORDER BY ri.report_date ASC"),
            {"uid": user_id, "name": item_name},
        ).fetchall()
        return [{"date": str(r[0]) if r[0] else None, "value": r[1], "unit": r[2]} for r in rows]

    @tool
    def get_triage_rules() -> list[dict]:
        """获取当前生效的三色分级规则，了解哪些指标阈值会被判定为红区/黄区。
        Returns:
            规则列表，每项含 rule_name/indicator_code/conditions/color_level
        """
        rows = db_session.execute(
            text("SELECT rule_name, indicator_code, conditions, color_level "
                 "FROM triage_rule WHERE is_active = 1 ORDER BY priority"),
        ).fetchall()
        return [{"rule_name": r[0], "indicator_code": r[1],
                 "conditions": r[2], "color_level": r[3]} for r in rows]

    return [search_knowledge, get_report_indicators, get_report_summary,
            get_user_history_reports, get_indicator_history, get_triage_rules]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/agents/__init__.py app/ai/agents/tools.py tests/ai/agents/test_tools.py && git commit -m "feat: add ai/agents/tools.py shared tool set for chat and interpretation agents"
```

---

## Task 12: ai/agents/chat_graph.py — Chat Agent StateGraph

**Files:**
- Create: `backend/app/ai/agents/chat_graph.py`
- Test: `backend/tests/ai/agents/test_chat_graph.py`

**Interfaces:**
- Produces: `build_chat_graph(hospital_id, db) -> CompiledGraph`，`run_chat_agent(hospital_id, db, session, user_message, user_id) -> AsyncIterator[dict]`
- Consumes: `make_tools`（Task 11）、`get_chat_model`（Task 3）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/agents/test_chat_graph.py`：

```python
import pytest
from unittest.mock import patch, MagicMock


def test_build_chat_graph_returns_compiled():
    """build_chat_graph 返回可编译的图"""
    with patch("app.ai.agents.chat_graph.get_chat_model") as mock_model, \
         patch("app.ai.agents.chat_graph.make_tools") as mock_tools:
        mock_model.return_value = MagicMock()
        mock_model.return_value.bind_tools.return_value = MagicMock()
        mock_tools.return_value = []

        from app.ai.agents.chat_graph import build_chat_graph
        graph = build_chat_graph("H001", MagicMock())
        assert graph is not None


def test_chat_state_has_knowledge_refs_accumulator():
    """ChatState 的 knowledge_refs 用累积 reducer"""
    from app.ai.agents.chat_graph import ChatState
    # TypedDict 无法直接检查 reducer，但能导入说明定义存在
    assert "knowledge_refs" in ChatState.__annotations__
    assert "messages" in ChatState.__annotations__
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/agents/test_chat_graph.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 创建 chat_graph.py**

创建 `backend/app/ai/agents/chat_graph.py`：

```python
from typing import TypedDict, Annotated, Optional, AsyncIterator, List
import json

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from sqlalchemy.orm import Session

from app.ai.llm import get_chat_model
from app.ai.agents.tools import make_tools
from app.config import settings

CHAT_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，为体检者提供易懂的健康咨询。

规则:
1. 基于报告数据和知识库回答，不编造信息
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"
6. 用户未关联报告时，引导其先上传报告以获取更精准建议

你有以下工具可用：
- search_knowledge: 搜索医学知识库
- get_report_indicators: 获取报告指标数据
- get_report_summary: 获取报告概览
- get_user_history_reports: 获取历年报告
- get_indicator_history: 获取指标历史趋势
- get_triage_rules: 获取三色分级规则

优先用工具获取信息，不要凭空回答。"""


def _accumulate_refs(existing: list, new: list) -> list:
    return existing + new


class ChatState(TypedDict):
    hospital_id: str
    session_id: int
    user_id: int
    report_id: Optional[int]
    messages: Annotated[list, add_messages]
    knowledge_refs: Annotated[list[dict], _accumulate_refs]
    final_response: str


def build_chat_graph(hospital_id: str, db: Session):
    """构造 chat Agent 的 LangGraph StateGraph"""
    tools = make_tools(hospital_id, db)
    model = get_chat_model(streaming=True).bind_tools(tools)
    tools_by_name = {t.name: t for t in tools}

    def agent_node(state: ChatState):
        sys_content = CHAT_SYSTEM_PROMPT
        if state.get("report_id"):
            sys_content += f"\n\n当前会话关联的报告 ID 是 {state['report_id']}，用户提问时可用 get_report_indicators 获取详细指标。"
        msgs = [SystemMessage(content=sys_content)] + state["messages"]
        resp = model.invoke(msgs)
        return {"messages": [resp]}

    def tool_node(state: ChatState):
        last_msg = state["messages"][-1]
        new_messages = []
        refs = []
        for call in last_msg.tool_calls:
            tool = tools_by_name.get(call["name"])
            if not tool:
                continue
            result = tool.invoke(call["args"])
            if call["name"] == "search_knowledge" and isinstance(result, list):
                refs.extend([
                    {"entry_id": r.get("entry_id"), "title": r.get("title")}
                    for r in result
                ])
            new_messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False),
                "tool_call_id": call["id"],
            })
        return {"messages": new_messages, "knowledge_refs": refs}

    def should_continue(state: ChatState) -> str:
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    g = StateGraph(ChatState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tool_node)
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", should_continue)
    g.add_edge("tools", "agent")
    return g.compile()


_session_locks: set[int] = set()


async def run_chat_agent(
    hospital_id: str,
    db: Session,
    session,
    user_message: str,
    user_id: int,
) -> AsyncIterator[dict]:
    """运行 chat Agent，yield SSE 事件 dict。
    事件类型：tool_status / token / done / error
    """
    from app.modules.chat import service as chat_service

    session_id = session.id
    if session_id in _session_locks:
        yield {"event": "error", "data": {"message": "正在处理上一条消息，请稍候"}}
        return
    _session_locks.add(session_id)

    try:
        chat_service.save_message(db, session_id, "user", user_message)

        history = chat_service.get_messages(db, session_id)
        history_msgs = [
            (HumanMessage(content=m.content) if m.role == "user"
             else AIMessage(content=m.content))
            for m in history[-settings.AGENT_MAX_ITERATIONS * 2:-1]
        ]

        graph = build_chat_graph(hospital_id, db)
        initial_state = {
            "hospital_id": hospital_id,
            "session_id": session_id,
            "user_id": user_id,
            "report_id": session.report_id,
            "messages": history_msgs + [HumanMessage(content=user_message)],
            "knowledge_refs": [],
            "final_response": "",
        }

        final_response = ""
        async for event in graph.astream_events(initial_state, version="v2"):
            kind = event.get("event")
            if kind == "on_tool_start":
                yield {"event": "tool_status", "data": {
                    "tool": event.get("name", ""), "status": "start"}}
            elif kind == "on_tool_end":
                yield {"event": "tool_status", "data": {
                    "tool": event.get("name", ""), "status": "end"}}
            elif kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    if not hasattr(chunk, "tool_call_chunks") or not chunk.tool_call_chunks:
                        final_response += chunk.content
                        yield {"event": "token", "data": {"content": chunk.content}}

        final_state = await graph.ainvoke(initial_state)
        refs = final_state.get("knowledge_refs", [])

        msg = chat_service.save_message(
            db, session_id, "assistant", final_response, knowledge_refs=refs or None
        )

        if not session.title:
            title = user_message[:50] + ("..." if len(user_message) > 50 else "")
            db.query(type(session)).filter(type(session).id == session_id).update({"title": title})
            db.commit()

        yield {"event": "done", "data": {"message_id": msg.id}}
    except Exception as e:
        yield {"event": "error", "data": {"message": f"AI 响应失败: {e}"}}
    finally:
        _session_locks.discard(session_id)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_chat_graph.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/ai/agents/chat_graph.py tests/ai/agents/test_chat_graph.py && git commit -m "feat: add ai/agents/chat_graph.py Chat Agent StateGraph with SSE streaming"
```

---

## Task 13: ai/agents/interp_graph.py — Interpretation Agent StateGraph

**Files:**
- Create: `backend/app/ai/agents/interp_graph.py`
- Test: `backend/tests/ai/agents/test_interp_graph.py`

**Interfaces:**
- Produces: `run_interpretation_agent(hospital_id, db, report_id) -> dict`
- Consumes: `make_tools`（Task 11）、`get_chat_model`（Task 3）、`rules_engine`（现有）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/agents/test_interp_graph.py`：

```python
from unittest.mock import patch, MagicMock


def test_interp_state_fields():
    """InterpState 含必需字段"""
    from app.ai.agents.interp_graph import InterpState
    assert "indicators" in InterpState.__annotations__
    assert "judgments" in InterpState.__annotations__
    assert "abnormal_indicators" in InterpState.__annotations__
    assert "agent_explanations" in InterpState.__annotations__
    assert "overall_level" in InterpState.__annotations__


def test_build_interp_graph_returns_compiled():
    """build_interp_graph 返回可编译的图"""
    with patch("app.ai.agents.interp_graph.get_chat_model") as mock_model, \
         patch("app.ai.agents.interp_graph.make_tools") as mock_tools:
        mock_model.return_value = MagicMock()
        mock_model.return_value.bind_tools.return_value = MagicMock()
        mock_tools.return_value = []

        from app.ai.agents.interp_graph import build_interp_graph
        graph = build_interp_graph("H001", MagicMock())
        assert graph is not None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/agents/test_interp_graph.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 创建 interp_graph.py**

创建 `backend/app/ai/agents/interp_graph.py`：

```python
from typing import TypedDict, List
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.llm import get_chat_model
from app.ai.agents.tools import make_tools
from app.modules.interpretation.rules_engine import rules_engine
from app.modules.interpretation.service import list_rules
from app.modules.interpretation.models import (
    ReportInterpretation, IndicatorJudgment,
)
from app.modules.report.models import ReportInfo, ReportIndicator
from app.core.rabbitmq import rabbitmq, TaskMessage

INTERP_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，
为体检者撰写易懂的指标解读和健康建议。

规则:
1. 绿区指标一笔带过，重点解读红区和黄区
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统的"注意饮食"
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"

你有以下工具可用：
- search_knowledge: 搜索医学知识库（对每个异常指标都应查询相关知识）
- get_triage_rules: 获取三色分级规则

对每个异常指标生成 explanation（解读）和 suggestion（建议），引用知识库注明来源。"""


class InterpBatchResult(TypedDict):
    """单指标的解读结果"""
    indicator_id: int
    explanation: str
    suggestion: str


class InterpState(TypedDict):
    hospital_id: str
    report_id: int
    indicators: List[dict]
    judgments: List[dict]
    abnormal_indicators: List[dict]
    agent_explanations: dict  # indicator_id -> {explanation, suggestion}
    knowledge_refs: dict      # indicator_id -> list[dict]
    overall_level: str
    red_count: int
    yellow_count: int
    green_count: int


def build_interp_graph(hospital_id: str, db: Session):
    """构造 interpretation Agent 的 LangGraph StateGraph"""

    def load_indicators(state: InterpState) -> dict:
        report_id = state["report_id"]
        rows = db.execute(
            text("SELECT id, item_name, item_name_standard, result_value, unit, "
                 "ref_range_low, ref_range_high FROM report_indicator WHERE report_id = :rid ORDER BY id"),
            {"rid": report_id},
        ).fetchall()
        indicators = [
            {"id": r[0], "item_name": r[1], "item_name_standard": r[2],
             "result_value": r[3], "unit": r[4],
             "ref_range_low": r[5], "ref_range_high": r[6]}
            for r in rows
        ]
        return {"indicators": indicators}

    def run_rules(state: InterpState) -> dict:
        rules = list_rules(db)
        rules_engine.load_rules(state["hospital_id"], [{
            "id": r.id, "rule_name": r.rule_name, "rule_type": r.rule_type,
            "indicator_code": r.indicator_code, "conditions": r.conditions,
            "color_level": r.color_level, "priority": r.priority, "is_active": r.is_active,
        } for r in rules])

        judgments = []
        red_count = yellow_count = green_count = 0
        for ind in state["indicators"]:
            ind_dict = {
                "item_name": ind["item_name"],
                "item_name_standard": ind["item_name_standard"],
                "result_value": ind["result_value"],
                "unit": ind["unit"],
                "ref_range_low": ind["ref_range_low"],
                "ref_range_high": ind["ref_range_high"],
            }
            result = rules_engine.evaluate(state["hospital_id"], ind_dict)

            deviation = result.deviation
            if deviation == "normal":
                try:
                    val = float(ind["result_value"] or 0)
                    ref_high = float(ind["ref_range_high"] or 0)
                    ref_low = float(ind["ref_range_low"] or 0)
                    if ref_high and val > ref_high:
                        deviation = "high"
                    elif ref_low and val < ref_low:
                        deviation = "low"
                except (ValueError, TypeError):
                    pass

            judgments.append({
                "indicator_id": ind["id"],
                "item_name": ind["item_name"],
                "result_value": ind["result_value"],
                "deviation": deviation,
                "color_level": result.color_level,
                "matched_rule_id": result.matched_rule_id,
            })

            if result.color_level == "red":
                red_count += 1
            elif result.color_level == "yellow":
                yellow_count += 1
            else:
                green_count += 1

        overall = "green"
        if red_count > 0:
            overall = "red"
        elif yellow_count > 0:
            overall = "yellow"

        return {
            "judgments": judgments,
            "overall_level": overall,
            "red_count": red_count,
            "yellow_count": yellow_count,
            "green_count": green_count,
        }

    def filter_abnormal(state: InterpState) -> dict:
        abnormal = [
            {**j, **{"item_name_standard": next(
                (i["item_name_standard"] for i in state["indicators"] if i["id"] == j["indicator_id"]),
                None
            ), "unit": next(
                (i["unit"] for i in state["indicators"] if i["id"] == j["indicator_id"]),
                None
            ), "ref_range_low": next(
                (i["ref_range_low"] for i in state["indicators"] if i["id"] == j["indicator_id"]),
                None
            ), "ref_range_high": next(
                (i["ref_range_high"] for i in state["indicators"] if i["id"] == j["indicator_id"]),
                None
            )}}
            for j in state["judgments"]
            if j["color_level"] in ("red", "yellow")
        ]
        return {"abnormal_indicators": abnormal}

    def agent_batch(state: InterpState) -> dict:
        if not state["abnormal_indicators"]:
            return {"agent_explanations": {}, "knowledge_refs": {}}

        tools = make_tools(state["hospital_id"], db)
        model = get_chat_model(streaming=False).bind_tools(tools)
        tools_by_name = {t.name: t for t in tools}

        indicator_lines = []
        for ind in state["abnormal_indicators"]:
            ref = f"{ind.get('ref_range_low','-')}-{ind.get('ref_range_high','-')}"
            indicator_lines.append(
                f"[ID:{ind['indicator_id']}] {ind['item_name']}: "
                f"值 {ind['result_value']}{ind.get('unit','')}, "
                f"参考区间 {ref}, {ind['deviation']}, {ind['color_level']}区"
            )
        indicators_text = "\n".join(indicator_lines)

        user_content = f"""以下是本报告的异常指标，请对每个查相关医学知识并生成解读+建议：

{indicators_text}

对每个指标调用 search_knowledge 查询相关知识，然后输出 JSON 数组，每个元素：
{{"indicator_id": int, "explanation": "解读文字", "suggestion": "建议文字"}}"""

        messages = [
            SystemMessage(content=INTERP_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ]

        max_iter = 8
        knowledge_refs = {}
        for _ in range(max_iter):
            resp = model.invoke(messages)
            messages.append(resp)
            if not (hasattr(resp, "tool_calls") and resp.tool_calls):
                break
            for call in resp.tool_calls:
                tool = tools_by_name.get(call["name"])
                if not tool:
                    continue
                result = tool.invoke(call["args"])
                if call["name"] == "search_knowledge" and isinstance(result, list):
                    for r in result:
                        ref_item = {"entry_id": r.get("entry_id"), "title": r.get("title")}
                        for ind in state["abnormal_indicators"]:
                            iid = ind["indicator_id"]
                            if iid not in knowledge_refs:
                                knowledge_refs[iid] = []
                            if ref_item not in knowledge_refs[iid]:
                                knowledge_refs[iid].append(ref_item)
                messages.append({
                    "role": "tool",
                    "content": json.dumps(result, ensure_ascii=False),
                    "tool_call_id": call["id"],
                })

        import json
        import re
        explanations = {}
        raw = resp.content if hasattr(resp, "content") else str(resp)
        try:
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                for item in parsed:
                    iid = item.get("indicator_id")
                    if iid:
                        explanations[iid] = {
                            "explanation": item.get("explanation", ""),
                            "suggestion": item.get("suggestion", ""),
                        }
        except (json.JSONDecodeError, AttributeError):
            pass

        for ind in state["abnormal_indicators"]:
            iid = ind["indicator_id"]
            if iid not in explanations:
                explanations[iid] = {"explanation": "", "suggestion": ""}

        return {"agent_explanations": explanations, "knowledge_refs": knowledge_refs}

    def persist(state: InterpState) -> dict:
        report_id = state["report_id"]

        db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == report_id,
        ).delete()
        db.commit()

        interp = ReportInterpretation(
            report_id=report_id, status="processing",
        )
        db.add(interp)
        db.commit()
        db.refresh(interp)

        for j in state["judgments"]:
            iid = j["indicator_id"]
            exp_data = state.get("agent_explanations", {}).get(iid, {})
            refs = state.get("knowledge_refs", {}).get(iid, [])
            db.add(IndicatorJudgment(
                interpretation_id=interp.id,
                indicator_id=iid,
                item_name=j["item_name"],
                result_value=j["result_value"],
                deviation=j["deviation"],
                color_level=j["color_level"],
                matched_rule_id=j["matched_rule_id"],
                explanation=exp_data.get("explanation", ""),
                suggestion=exp_data.get("suggestion", ""),
                knowledge_refs=refs or None,
            ))

        interp.red_count = state["red_count"]
        interp.yellow_count = state["yellow_count"]
        interp.green_count = state["green_count"]
        interp.overall_level = state["overall_level"]
        interp.status = "completed"
        interp.completed_at = datetime.utcnow()
        db.commit()

        rabbitmq.publish(TaskMessage(
            task_type="interpretation", hospital_id=state["hospital_id"], priority=0,
            payload={"event": "interpretation_done", "report_id": report_id,
                     "hospital_id": state["hospital_id"]},
        ))
        return {}

    g = StateGraph(InterpState)
    g.add_node("load_indicators", load_indicators)
    g.add_node("run_rules", run_rules)
    g.add_node("filter_abnormal", filter_abnormal)
    g.add_node("agent_batch", agent_batch)
    g.add_node("persist", persist)
    g.set_entry_point("load_indicators")
    g.add_edge("load_indicators", "run_rules")
    g.add_edge("run_rules", "filter_abnormal")
    g.add_edge("filter_abnormal", "agent_batch")
    g.add_edge("agent_batch", "persist")
    g.add_edge("persist", END)
    return g.compile()


def run_interpretation_agent(hospital_id: str, db: Session, report_id: int) -> dict:
    """同步运行 interpretation 图，返回最终状态"""
    report = db.query(ReportInfo).filter(ReportInfo.id == report_id).first()
    if not report:
        return {}

    existing = db.query(ReportInterpretation).filter(
        ReportInterpretation.report_id == report_id,
        ReportInterpretation.status == "completed",
    ).first()
    if existing:
        return {}

    graph = build_interp_graph(hospital_id, db)
    try:
        final_state = graph.invoke({
            "hospital_id": hospital_id,
            "report_id": report_id,
            "indicators": [],
            "judgments": [],
            "abnormal_indicators": [],
            "agent_explanations": {},
            "knowledge_refs": {},
            "overall_level": "green",
            "red_count": 0,
            "yellow_count": 0,
            "green_count": 0,
        })
        return final_state
    except Exception as e:
        interp = db.query(ReportInterpretation).filter(
            ReportInterpretation.report_id == report_id,
            ReportInterpretation.status == "processing",
        ).first()
        if interp:
            interp.retry_count += 1
            interp.status = "failed" if interp.retry_count >= 3 else "pending"
            db.commit()
        raise
```

- [ ] **Step 4: 在 interp_graph.py 顶部补充 json import**

编辑 `backend/app/ai/agents/interp_graph.py`，在文件最顶部的 import 区追加：

```python
import json
import re
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_interp_graph.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/ai/agents/interp_graph.py tests/ai/agents/test_interp_graph.py && git commit -m "feat: add ai/agents/interp_graph.py Interpretation Agent StateGraph with batch processing"
```

---

## Task 14: ai/agents/__init__.py — 对外高层 API

**Files:**
- Modify: `backend/app/ai/agents/__init__.py`（Task 11 创建的空文件）

**Interfaces:**
- Produces: `run_chat_agent`/`run_interpretation_agent` 可从 `app.ai.agents` 直接导入

- [ ] **Step 1: 填充 __init__.py**

编辑 `backend/app/ai/agents/__init__.py`：

```python
from app.ai.agents.chat_graph import run_chat_agent, build_chat_graph
from app.ai.agents.interp_graph import run_interpretation_agent, build_interp_graph
from app.ai.agents.tools import make_tools
```

- [ ] **Step 2: 验证可导入**

Run: `cd backend && uv run python -c "from app.ai.agents import run_chat_agent, run_interpretation_agent; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
cd backend && git add app/ai/agents/__init__.py && git commit -m "feat: expose ai/agents high-level API"
```

---

## Task 15: 迁移 chat/service.py + stream.py

**Files:**
- Modify: `backend/app/modules/chat/service.py`
- Modify: `backend/app/modules/chat/stream.py`
- Test: `backend/tests/ai/agents/test_chat_migration.py`

**Interfaces:**
- Consumes: `ai.agents.run_chat_agent`（Task 12/14）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/agents/test_chat_migration.py`：

```python
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.mark.asyncio
async def test_process_chat_stream_yields_sse_events():
    """process_chat_stream 调 run_chat_agent 并转 SSE 事件"""
    with patch("app.modules.chat.service.run_chat_agent") as mock_run:
        async def fake_agent(*args, **kwargs):
            yield {"event": "tool_status", "data": {"tool": "search_knowledge", "status": "start"}}
            yield {"event": "token", "data": {"content": "你好"}}
            yield {"event": "done", "data": {"message_id": 1}}
        mock_run.side_effect = fake_agent

        from app.modules.chat import service
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.id = 1
        mock_session.report_id = None
        mock_session.title = "test"

        with patch.object(service, "save_message"), \
             patch.object(service, "get_messages", return_value=[]):
            events = []
            async for ev in service.process_chat_stream(mock_db, mock_session, "你好", 1):
                events.append(ev)
            assert len(events) == 3
            assert events[1]["event"] == "token"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/agents/test_chat_migration.py -v`
Expected: FAIL — `process_chat_stream` 还是同步的旧实现

- [ ] **Step 3: 改造 chat/service.py**

编辑 `backend/app/modules/chat/service.py`。

替换文件开头的 import 区块（第 1-7 行）为：

```python
from typing import Iterator, List, Optional, AsyncIterator
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.chat.models import ChatSession, ChatMessage
from app.core.llm_client import llm_client  # noqa: F401 — 将在 Task 17 删除
from app.ai.agents import run_chat_agent
```

替换 `process_chat_stream` 函数（第 174-232 行）为：

```python
async def process_chat_stream(
    db: Session,
    session: ChatSession,
    user_message: str,
    user_id: int,
) -> AsyncIterator[dict]:
    """处理一条用户消息，异步 yield SSE 事件 dict"""

    if session.id in _session_locks:
        yield {"event": "error", "data": {"message": "正在处理上一条消息，请稍候"}}
        return
    _session_locks.add(session.id)

    try:
        async for event in run_chat_agent(
            session.hospital_id, db, session, user_message, user_id,
        ):
            yield event
    finally:
        _session_locks.discard(session.id)
```

删除以下函数（已被 Agent 替代）：
- `_load_report_context`（第 128-146 行）
- `_build_knowledge_context`（第 149-160 行）
- `_get_knowledge_refs`（第 163-169 行）

保留的函数：`CHAT_SYSTEM_PROMPT`（移到 chat_graph.py 已有，此处可删）、`MAX_HISTORY_ROUNDS`、Session CRUD、`_get_report_date_note`、`_get_latest_report`、`list_sessions`、`get_session`、`delete_session`、`get_messages`、`save_message`、`create_session`、`update_session_report`。

删除 `CHAT_SYSTEM_PROMPT` 常量（已搬到 `ai/agents/chat_graph.py`）。

- [ ] **Step 4: 改造 chat/stream.py**

编辑 `backend/app/modules/chat/stream.py`，替换为：

```python
import json
from starlette.responses import StreamingResponse


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_stream(agent_gen):
    """将 async agent generator 包装为 SSE StreamingResponse"""

    async def event_generator():
        async for ev in agent_gen:
            event_type = ev.get("event", "message")
            event_data = ev.get("data", {})
            yield sse_event(event_type, event_data)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 5: 改造 chat/router.py 的 send_message**

编辑 `backend/app/modules/chat/router.py`，将 `send_message` 端点改为 async：

```python
@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: int,
    data: SendMessageRequest,
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    session = service.get_session(db, session_id, current_user.user_id)
    if not session:
        raise NotFoundException(detail="Session not found")
    token_gen = service.process_chat_stream(
        db, session, data.content, current_user.user_id
    )
    return await sse_stream(token_gen)
```

- [ ] **Step 6: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_chat_migration.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd backend && git add app/modules/chat/service.py app/modules/chat/stream.py app/modules/chat/router.py tests/ai/agents/test_chat_migration.py && git commit -m "refactor: migrate chat module to LangGraph Agent with async SSE streaming"
```

---

## Task 16: 迁移 interpretation/service.py + worker.py

**Files:**
- Modify: `backend/app/modules/interpretation/service.py`
- Modify: `backend/app/modules/interpretation/worker.py`
- Test: `backend/tests/ai/agents/test_interp_migration.py`

**Interfaces:**
- Consumes: `ai.agents.run_interpretation_agent`（Task 13/14）

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/ai/agents/test_interp_migration.py`：

```python
from unittest.mock import patch, MagicMock


def test_worker_calls_run_interpretation_agent():
    """worker 消费任务后调 run_interpretation_agent"""
    with patch("app.modules.interpretation.worker.run_interpretation_agent") as mock_run, \
         patch("app.modules.interpretation.worker.get_hospital_db") as mock_db_fn:
        mock_db = MagicMock()
        mock_db_fn.return_value = iter([mock_db])

        from app.modules.interpretation.worker import handle_interpretation_task
        handle_interpretation_task({
            "payload": {"report_id": 1, "hospital_id": "H001"}
        })
        mock_run.assert_called_once()


def test_worker_skips_event_messages():
    """worker 跳过 event 通知消息"""
    with patch("app.modules.interpretation.worker.run_interpretation_agent") as mock_run:
        from app.modules.interpretation.worker import handle_interpretation_task
        handle_interpretation_task({"payload": {"event": "interpretation_done"}})
        mock_run.assert_not_called()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd backend && uv run pytest tests/ai/agents/test_interp_migration.py -v`
Expected: FAIL

- [ ] **Step 3: 改造 interpretation/service.py**

编辑 `backend/app/modules/interpretation/service.py`。

删除以下内容：
- `process_interpretation` 函数（第 60-170 行）— 替换为薄包装：
- `_fetch_knowledge` 函数（第 173-187 行）— 删除
- `import httpx`（第 11 行）— 删除
- `from app.core.llm_client import llm_client`（第 9 行）— 删除

在文件顶部 import 区追加：

```python
from app.ai.agents import run_interpretation_agent
```

在删除 `process_interpretation` 的位置添加：

```python
def process_interpretation(db: Session, report_id: int, hospital_id: str):
    """触发 interpretation Agent 处理（薄包装，实际逻辑在 ai/agents/interp_graph.py）"""
    run_interpretation_agent(hospital_id, db, report_id)
```

保留：Triage Rules CRUD、`get_interpretation`、`get_judgments`、`get_high_risk_list`。

- [ ] **Step 4: 改造 interpretation/worker.py**

编辑 `backend/app/modules/interpretation/worker.py`，替换为：

```python
from app.core.database import get_hospital_db
from app.core.rabbitmq import rabbitmq
from app.ai.agents import run_interpretation_agent


def handle_interpretation_task(message: dict):
    payload = message.get("payload", {})
    if payload.get("event"):
        return
    report_id = payload.get("report_id")
    hospital_id = payload.get("hospital_id")

    if not report_id:
        return

    db = next(get_hospital_db(hospital_id))
    try:
        run_interpretation_agent(hospital_id, db, report_id)
    except Exception as e:
        print(f"Interpretation failed for report {report_id}: {e}")
    finally:
        db.close()


def start_worker():
    while True:
        try:
            rabbitmq.consume("interpretation.urgent", handle_interpretation_task)
            rabbitmq.consume("interpretation.normal", handle_interpretation_task)
            print("Interpretation worker started, waiting for tasks...")
            rabbitmq.start_consuming()
        except Exception as e:
            print(f"Worker disconnected: {e}, reconnecting in 3s...")
            import time
            time.sleep(3)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `cd backend && uv run pytest tests/ai/agents/test_interp_migration.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add app/modules/interpretation/service.py app/modules/interpretation/worker.py tests/ai/agents/test_interp_migration.py && git commit -m "refactor: migrate interpretation module to LangGraph Agent"
```

---

## Task 17: 删除旧 core 文件 + 更新 main.py 启动

**Files:**
- Delete: `backend/app/core/milvus.py`
- Delete: `backend/app/core/embedding.py`
- Delete: `backend/app/core/llm_client.py`
- Delete: `backend/app/core/doc_parser.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 搜索旧文件的残留引用**

Run: `cd backend && grep -rn "from app.core.milvus\|from app.core.embedding\|from app.core.llm_client\|from app.core.doc_parser\|core\.milvus\|core\.embedding\|core\.llm_client\|core\.doc_parser" app/ --include="*.py"`

检查输出，逐个修复残留引用。预期残留位置：
- `chat/service.py` 里的 `from app.core.llm_client import llm_client`（Task 15 已加 noqa，需确认删除）
- 任何其他模块的引用

- [ ] **Step 2: 修复 chat/service.py 残留 import**

编辑 `backend/app/modules/chat/service.py`，删除 `from app.core.llm_client import llm_client  # noqa: F401` 这行。

- [ ] **Step 3: 删除旧文件**

Run: `cd backend && rm app/core/milvus.py app/core/embedding.py app/core/llm_client.py app/core/doc_parser.py`

- [ ] **Step 4: 更新 main.py 启动时初始化 milvus**

编辑 `backend/app/main.py`，在 `create_app` 函数的 `app = FastAPI(...)` 之后、`add_middleware` 之前追加：

```python
    from app.ai.config import ensure_milvus_started
    ensure_milvus_started()
```

- [ ] **Step 5: 验证无残留引用且可导入**

Run: `cd backend && grep -rn "core.milvus\|core.embedding\|core.llm_client\|core.doc_parser" app/ --include="*.py" || echo "no residuals"`
Expected: `no residuals`

Run: `cd backend && uv run python -c "from app.main import app; print('app ok')"`
Expected: `app ok`

- [ ] **Step 6: 运行全部测试**

Run: `cd backend && uv run pytest tests/ -v`
Expected: 全部 PASS（或只有外部依赖相关的 skip）

- [ ] **Step 7: Commit**

```bash
cd backend && git add -A && git commit -m "refactor: delete hand-written core/milvus,embedding,llm_client,doc_parser; init milvus in main.py"
```

---

## Task 18: 更新启动脚本

**Files:**
- Modify: `start_local.sh`
- Modify: `start.sh`
- Modify: `start_windows_local.bat`

- [ ] **Step 1: 在 start_local.sh 增加 reranker 服务启动**

编辑 `start_local.sh`，在 "7.5 Start vLLM Embedding server" 段之后、"8. Start backend" 段之前，插入：

```bash
# ── 7.6 Start Reranker service (port 8003) ──────────────────────
log "Starting Reranker service (port 8003)..."
RERANKER_DIR="$BACKEND_DIR/reranker_service"
if [[ -d "$RERANKER_DIR" ]]; then
    pushd "$RERANKER_DIR" >/dev/null
    export HF_ENDPOINT=https://hf-mirror.com
    PATH="$HOME/.local/bin:$PATH" nohup uv run uvicorn main:app --host 127.0.0.1 --port 8003 > /tmp/reranker.log 2>&1 &
    RERANKER_PID=$!
    popd >/dev/null
    log "Reranker service starting (PID: $RERANKER_PID, log: /tmp/reranker.log)"
else
    warn "reranker_service dir not found, skipping"
fi
```

在 `cleanup()` 函数的 kill 列表追加 `kill $RERANKER_PID 2>/dev/null || true`。

在最终 Summary 的 echo 列表追加：
```
  Reranker:    http://localhost:8003  (log: /tmp/reranker.log)
```

- [ ] **Step 2: 同步修改 start.sh 和 start_windows_local.bat**

对 `start.sh` 重复 Step 1 的改动（bash 语法相同）。

对 `start_windows_local.bat` 做等价改动（Windows bat 语法：`start /B uv run uvicorn main:app --port 8003`）。

- [ ] **Step 3: 验证 start_local.sh 语法**

Run: `bash -n /root/autodl-tmp/hospitalKnowledgeBase/start_local.sh`
Expected: 无输出（语法正确）

- [ ] **Step 4: Commit**

```bash
cd /root/autodl-tmp/hospitalKnowledgeBase && git add start_local.sh start.sh start_windows_local.bat && git commit -m "ops: add reranker service to startup scripts"
```

---

## Task 19: 数据迁移脚本

**Files:**
- Create: `backend/scripts/reindex_existing.py`

- [ ] **Step 1: 创建迁移脚本**

创建 `backend/scripts/reindex_existing.py`：

```python
"""一次性迁移脚本：将 MySQL 中的知识库条目重新索引到 LlamaIndex MilvusVectorStore。

用途：旧 collection schema（手写）与 LlamaIndex schema 不兼容，需 drop 后从 MySQL 重建。
运行：cd backend && uv run python scripts/reindex_existing.py [hospital_id]
不传 hospital_id 则重建所有医院的 knowledge_entry。
"""
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.config import settings
from app.core.database import get_hospital_db
from app.ai.config import ensure_milvus_started
from app.ai import rag as ai_rag
from app.modules.knowledge.models import KnowledgeEntry


def reindex_hospital(hospital_id: str):
    print(f"Reindexing hospital {hospital_id}...")
    ensure_milvus_started()

    db = next(get_hospital_db(hospital_id))
    try:
        entries = db.query(KnowledgeEntry).filter(KnowledgeEntry.status == 1).all()
        entry_dicts = [
            {"id": e.id, "title": e.title, "content": e.content,
             "category_id": e.category_id, "source_file": e.source_file}
            for e in entries
        ]
    finally:
        db.close()

    if not entry_dicts:
        print(f"  Hospital {hospital_id}: no entries, skipping")
        return

    ai_rag.reindex_hospital(hospital_id, entry_dicts)
    print(f"  Hospital {hospital_id}: reindexed {len(entry_dicts)} entries")


def main():
    if len(sys.argv) > 1:
        reindex_hospital(sys.argv[1])
    else:
        from app.core.database import get_all_hospital_ids
        for hid in get_all_hospital_ids():
            reindex_hospital(hid)
    print("Done.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 验证脚本语法**

Run: `cd backend && uv run python -c "import ast; ast.parse(open('scripts/reindex_existing.py').read()); print('syntax ok')"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
cd backend && git add scripts/reindex_existing.py && git commit -m "feat: add data migration script for reindexing knowledge base to LlamaIndex"
```

---

## Task 20: 集成验证

**Files:**
- Test: `backend/tests/ai/test_integration.py`

- [ ] **Step 1: 写集成测试（mock 外部依赖）**

创建 `backend/tests/ai/test_integration.py`：

```python
"""集成测试：验证 ai 层与 modules 层的端到端衔接（mock LLM/Milvus/RabbitMQ）。"""
from unittest.mock import patch, MagicMock


def test_knowledge_crud_to_rag_pipeline():
    """create_entry → ai.rag.index_documents → search → ai.rag.search 全链路"""
    with patch("app.ai.rag.RAGIndexer") as MockIndexer, \
         patch("app.ai.rag.RAGRetriever") as MockRetriever:
        mock_idx = MagicMock()
        MockIndexer.return_value = mock_idx
        mock_idx.index_documents.return_value = ["n1"]

        mock_ret = MagicMock()
        MockRetriever.return_value = mock_ret
        from app.modules.knowledge.schemas import SearchResult
        mock_ret.retrieve.return_value = [SearchResult(
            entry_id=1, title="血糖知识", content="空腹血糖正常值3.9-6.1",
            category_id=2, score=0.95
        )]

        from app.ai.rag import index_documents, search
        ids = index_documents("H001", [], 2, "test.pdf")
        assert ids == ["n1"]

        results = search("H001", "空腹血糖")
        assert results[0].title == "血糖知识"
        assert "3.9-6.1" in results[0].content


def test_agent_tools_available_in_graph():
    """chat 和 interp 图都能拿到 make_tools 产出的工具集"""
    with patch("app.ai.agents.chat_graph.get_chat_model") as mock_model, \
         patch("app.ai.agents.interp_graph.get_chat_model"), \
         patch("app.ai.agents.tools.ai_rag"):
        mock_model.return_value = MagicMock()
        mock_model.return_value.bind_tools.return_value = MagicMock()

        from app.ai.agents.tools import make_tools
        from app.ai.agents.chat_graph import build_chat_graph
        from app.ai.agents.interp_graph import build_interp_graph

        tools = make_tools("H001", MagicMock())
        assert len(tools) == 6

        chat_g = build_chat_graph("H001", MagicMock())
        interp_g = build_interp_graph("H001", MagicMock())
        assert chat_g is not None
        assert interp_g is not None
```

- [ ] **Step 2: 运行集成测试**

Run: `cd backend && uv run pytest tests/ai/test_integration.py -v`
Expected: PASS

- [ ] **Step 3: 运行全部测试套件**

Run: `cd backend && uv run pytest tests/ -v --tb=short`
Expected: 全部 PASS

- [ ] **Step 4: 验证后端可完整启动（无外部依赖时 mock 检查）**

Run: `cd backend && uv run python -c "from app.main import app; print('routes:', len(app.routes))"`
Expected: 输出路由数量，无异常

- [ ] **Step 5: Commit**

```bash
cd backend && git add tests/ai/test_integration.py && git commit -m "test: add integration tests for ai layer end-to-end pipeline"
```

---

## Self-Review

### Spec coverage

| Spec 章节 | 覆盖任务 |
|-----------|----------|
| §1.1 分层与依赖方向 | Task 1-17（整体结构） |
| §1.2 删除与新增文件 | Task 4/5/6/8/9/11/12/13/14（新增），Task 17（删除） |
| §1.3 依赖 | Task 1 |
| §1.4 配置扩展 | Task 1 |
| §2.1 store.py | Task 4 |
| §2.2 readers.py | Task 5 |
| §2.3 indexer.py | Task 6 |
| §2.4 retriever.py | Task 8 |
| §2.5 Reranker 服务 | Task 7 |
| §2.6 对外 API | Task 9 |
| §3.1 llm.py | Task 3 |
| §3.2 tools.py | Task 11 |
| §3.3 chat_graph.py | Task 12 |
| §3.4 SSE 流式 | Task 12 + Task 15 |
| §3.5 interp_graph.py | Task 13 |
| §3.6 对外 API | Task 14 |
| §3.7 Prompt | Task 12 + Task 13 |
| §4.1 模块改造清单 | Task 10/15/16/17 |
| §4.2 API 契约不变 | Task 10/15/16（router 不改） |
| §4.3 数据迁移 | Task 19 |
| §4.4 YAGNI 边界 | 未超出 |
| §4.5 启动流程 | Task 17 + Task 18 |
| §4.6 测试策略 | 每个 Task 含单元测试 + Task 20 集成测试 |

无遗漏。

### Placeholder scan

无 TBD/TODO/placeholder。每个步骤含具体代码或具体命令。

### Type consistency

- `make_tools(hospital_id, db_session)` 签名在 Task 11 定义，Task 12/13 调用一致
- `run_chat_agent(hospital_id, db, session, user_message, user_id)` 在 Task 12 定义，Task 14/15 调用一致
- `run_interpretation_agent(hospital_id, db, report_id)` 在 Task 13 定义，Task 14/16 调用一致
- `SearchResult` schema 在 Task 8/9/10/11 使用一致
- `ai.rag.search/index_documents/delete_vectors/reindex_hospital` 在 Task 9 定义，Task 10/11 调用一致

无类型不一致。
