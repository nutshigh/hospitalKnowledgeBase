# Chat 模块 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在用户端新增 AI 聊天功能，支持报告内嵌聊天和独立聊天页面，AI 基于知识库 RAG + 报告数据流式问答。

**Architecture:** 先重构 LLM Client 为策略模式（本地 vLLM / 远端 OpenAI 兼容 API），再新增 Chat 后端模块（session/message 管理 + SSE 流式 + RAG），最后实现前端 Chat 组件。21 个任务。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / httpx / React 18 + TypeScript / Ant Design 5 / Zustand

---

## File Map

```
Backend (新建/修改):
├── backend/app/config.py                          [修改] 新增 LLM_PROVIDER, REMOTE_LLM_* 配置
├── backend/.env.example                           [修改] 新增远端 LLM 配置项
├── backend/app/core/llm_client.py                 [重写] 策略模式多后端 + stream 支持
├── backend/app/main.py                            [修改] 注册 chat router
├── backend/app/modules/chat/
│   ├── __init__.py                                [新建] 
│   ├── models.py                                  [新建] ChatSession, ChatMessage
│   ├── schemas.py                                 [新建] Pydantic 请求/响应
│   ├── service.py                                 [新建] 会话 + 消息 + RAG + LLM 调用
│   ├── stream.py                                  [新建] SSE StreamingResponse 辅助
│   └── router.py                                  [新建] FastAPI 路由

Frontend (新建/修改):
├── frontend/packages/user-portal/src/
│   ├── router.tsx                                 [修改] 新增 /chat 路由
│   ├── hooks/useChatStream.ts                     [新建] SSE EventSource hook
│   ├── stores/chatStore.ts                        [新建] Zustand 会话状态
│   ├── components/
│   │   ├── ChatBubble.tsx                         [新建] 消息气泡
│   │   ├── ChatInput.tsx                          [新建] 输入框
│   │   ├── ChatPanel.tsx                          [新建] 聊天面板
│   │   └── SessionDrawer.tsx                      [新建] 会话列表抽屉
│   ├── pages/
│   │   ├── ChatPage.tsx                           [新建] 独立聊天页
│   │   └── ReportDetailPage.tsx                   [修改] 嵌入 ChatPanel
```

---

### Task 1: 新增 LLM 配置项

**Files:**
- Modify: `backend/app/config.py:27-31`
- Modify: `backend/.env.example:18-21`

- [ ] **Step 1: 修改 config.py，新增 LLM Provider 配置**

```python
# 在 VLLM 配置块之后追加

    # LLM Provider
    LLM_PROVIDER: str = "local"  # local | remote

    # Remote LLM (OpenAI 兼容 API)
    REMOTE_LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    REMOTE_LLM_API_KEY: str = ""
    REMOTE_LLM_MODEL: str = "deepseek-chat"
    REMOTE_LLM_MAX_TOKENS: int = 4096
    REMOTE_LLM_TEMPERATURE: float = 0.1

    # LLM 通用
    LLM_TIMEOUT_SECONDS: int = 120
```

- [ ] **Step 2: 修改 .env.example，追加新配置项**

```bash
# === 后端选择 ===
LLM_PROVIDER=local          # local | remote

# === 本地 vLLM ===
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_CHAT_MODEL=qwen2.5
VLLM_VISION_MODEL=qwen-vl
VLLM_EMBED_MODEL=bge-m3

# === 远端 API (OpenAI 兼容) ===
REMOTE_LLM_BASE_URL=https://api.deepseek.com/v1
REMOTE_LLM_API_KEY=sk-xxxxxxxx
REMOTE_LLM_MODEL=deepseek-chat
REMOTE_LLM_MAX_TOKENS=4096
REMOTE_LLM_TEMPERATURE=0.1

# === 通用 ===
LLM_TIMEOUT_SECONDS=120
```

- [ ] **Step 3: 验证配置加载**

Run: `cd backend && uv run python -c "from app.config import settings; print(settings.LLM_PROVIDER); print(settings.REMOTE_LLM_MODEL)"`
Expected: 输出 `local` 和 `deepseek-chat`

- [ ] **Step 4: Commit**

```bash
git add backend/app/config.py backend/.env.example
git commit -m "feat: add multi-provider LLM config (local vLLM / remote OpenAI-compatible)"
```

---

### Task 2: 重构 LLM Client — 策略模式 + stream

**Files:**
- Rewrite: `backend/app/core/llm_client.py`

- [ ] **Step 1: 重写 llm_client.py**

```python
from abc import ABC, abstractmethod
from typing import Iterator
from httpx import Client, Timeout
from app.config import settings

SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，
为体检者撰写易懂的指标解读和健康建议。

规则:
1. 绿区指标一笔带过，重点解读红区和黄区
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统的"注意饮食"
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"
"""


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], temperature: float, max_tokens: int) -> str: ...

    @abstractmethod
    def chat_stream(self, messages: list[dict], temperature: float, max_tokens: int) -> Iterator[str]: ...


class OpenAICompatProvider(LLMProvider):
    """OpenAI 兼容 API 实现 — vLLM 和远端 API 统一使用此 Provider"""
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.client = Client(timeout=Timeout(timeout))

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 1024) -> str:
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

    def chat_stream(self, messages: list[dict], temperature: float = 0.1, max_tokens: int = 1024) -> Iterator[str]:
        with self.client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    import json
                    try:
                        delta = json.loads(data_str)["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue


class LLMClient:
    def __init__(self):
        if settings.LLM_PROVIDER == "remote":
            self._provider = OpenAICompatProvider(
                base_url=settings.REMOTE_LLM_BASE_URL,
                model=settings.REMOTE_LLM_MODEL,
                api_key=settings.REMOTE_LLM_API_KEY,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
        else:
            self._provider = OpenAICompatProvider(
                base_url=settings.VLLM_BASE_URL,
                model=settings.VLLM_CHAT_MODEL,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )

    def chat(self, messages: list[dict], stream: bool = False,
             temperature: float | None = None, max_tokens: int | None = None) -> str | Iterator[str]:
        temp = temperature if temperature is not None else (settings.REMOTE_LLM_TEMPERATURE if settings.LLM_PROVIDER == "remote" else 0.1)
        mt = max_tokens if max_tokens is not None else (settings.REMOTE_LLM_MAX_TOKENS if settings.LLM_PROVIDER == "remote" else 1024)
        if stream:
            return self._provider.chat_stream(messages, temperature=temp, max_tokens=mt)
        return self._provider.chat(messages, temperature=temp, max_tokens=mt)

    def interpret_indicator(self, indicator: dict, knowledge_context: str) -> str:
        prompt = f"""## 本次报告数据
| 指标 | 结果 | 参考区间 | 判定 |
|------|------|----------|------|
| {indicator.get('item_name', '')} | {indicator.get('result_value', '')} | {indicator.get('ref_range_low', '')}-{indicator.get('ref_range_high', '')} | {indicator.get('deviation', '')}({indicator.get('color_level', '')}) |

## 参考知识库
{knowledge_context if knowledge_context else '无相关知识库条目'}

请解读这个指标，给出健康建议。"""
        return self.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])

    def generate_summary(self, report_summary: str, knowledge_context: str) -> str:
        prompt = f"""## 报告概况
{report_summary}

## 参考知识库
{knowledge_context if knowledge_context else '无相关知识库条目'}

请生成综合健康小结。"""
        return self.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])


llm_client = LLMClient()
```

- [ ] **Step 2: 验证非流式调用**

Run: `cd backend && uv run python -c "from app.core.llm_client import llm_client; r = llm_client.chat([{'role':'user','content':'你好'}], max_tokens=20); print(r)"`
Expected: 输出 LLM 返回的文本（需本地 vLLM 运行）

- [ ] **Step 3: 验证流式调用**

Run: `cd backend && uv run python -c "from app.core.llm_client import llm_client; [print(t, end='', flush=True) for t in llm_client.chat([{'role':'user','content':'你好'}], stream=True, max_tokens=20)]"`
Expected: 逐 token 输出

- [ ] **Step 4: Commit**

```bash
git add backend/app/core/llm_client.py
git commit -m "refactor: LLM client multi-provider strategy pattern with stream support"
```

---

### Task 3: 创建 Chat 数据库模型

**Files:**
- Create: `backend/app/modules/chat/__init__.py`
- Create: `backend/app/modules/chat/models.py`

- [ ] **Step 1: 创建 __init__.py**

```python
```

- [ ] **Step 2: 创建 models.py**

```python
from sqlalchemy import Column, BigInteger, String, Text, Integer, DateTime, ForeignKey, JSON, func
from app.models.base import Base


class ChatSession(Base):
    __tablename__ = "chat_session"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    hospital_id = Column(String(32), nullable=False)
    report_id = Column(BigInteger, nullable=True)
    title = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, ForeignKey("chat_session.id"), nullable=False)
    role = Column(String(10), nullable=False)
    content = Column(Text, nullable=False)
    knowledge_refs = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
```

- [ ] **Step 3: 验证模型可导入**

Run: `cd backend && uv run python -c "from app.modules.chat.models import ChatSession, ChatMessage; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/modules/chat/
git commit -m "feat: add ChatSession and ChatMessage SQLAlchemy models"
```

---

### Task 4: 创建 Chat Pydantic Schemas

**Files:**
- Create: `backend/app/modules/chat/schemas.py`

- [ ] **Step 1: 创建 schemas.py**

```python
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class CreateSessionRequest(BaseModel):
    report_id: Optional[int] = None


class SessionResponse(BaseModel):
    id: int
    user_id: int
    report_id: Optional[int] = None
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    knowledge_refs: Optional[List[dict]] = None
    created_at: datetime

    class Config:
        from_attributes = True
```

- [ ] **Step 2: 验证 Schema 可导入**

Run: `cd backend && uv run python -c "from app.modules.chat.schemas import CreateSessionRequest, SendMessageRequest; print(CreateSessionRequest(report_id=1)); print(SendMessageRequest(content='hello'))"`
Expected: 输出两个对象

- [ ] **Step 3: Commit**

```bash
git add backend/app/modules/chat/schemas.py
git commit -m "feat: add chat Pydantic schemas"
```

---

### Task 5: 创建 Chat Service

**Files:**
- Create: `backend/app/modules/chat/service.py`

- [ ] **Step 1: 创建 service.py**

```python
from typing import Iterator, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.chat.models import ChatSession, ChatMessage
from app.modules.knowledge import service as knowledge_service
from app.modules.knowledge.schemas import SearchResult
from app.core.llm_client import llm_client

CHAT_SYSTEM_PROMPT = """你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，为体检者提供易懂的健康咨询。

规则:
1. 基于报告数据和知识库回答，不编造信息
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"
6. 用户未关联报告时，引导其先上传报告以获取更精准建议"""

MAX_HISTORY_ROUNDS = 20


# ---- Session CRUD ----

def create_session(db: Session, user_id: int, hospital_id: str,
                   report_id: Optional[int] = None) -> ChatSession:
    session = ChatSession(user_id=user_id, hospital_id=hospital_id, report_id=report_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def list_sessions(db: Session, user_id: int) -> List[ChatSession]:
    return (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )


def get_session(db: Session, session_id: int, user_id: int) -> Optional[ChatSession]:
    return (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .first()
    )


def delete_session(db: Session, session_id: int, user_id: int) -> bool:
    session = get_session(db, session_id, user_id)
    if not session:
        return False
    db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return True


# ---- Messages ----

def get_messages(db: Session, session_id: int) -> List[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )


def save_message(db: Session, session_id: int, role: str, content: str,
                 knowledge_refs: Optional[List[dict]] = None) -> ChatMessage:
    msg = ChatMessage(session_id=session_id, role=role, content=content,
                      knowledge_refs=knowledge_refs)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


# ---- Context Building ----

def _load_report_context(db: Session, report_id: int) -> str:
    """加载报告的结构化指标数据作为上下文"""
    rows = db.execute(
        text(
            "SELECT item_name, result_value, unit, ref_range_low, ref_range_high "
            "FROM report_indicator WHERE report_id = :rid ORDER BY id"
        ),
        {"rid": report_id},
    ).fetchall()

    if not rows:
        return "报告正在解析中，暂无指标数据"

    lines = ["| 指标 | 结果 | 参考区间 |", "|------|------|----------|"]
    for r in rows:
        ref = f"{r.ref_range_low or '-'}-{r.ref_range_high or '-'}"
        unit = r.unit or ""
        lines.append(f"| {r.item_name} | {r.result_value or '-'}{unit} | {ref} |")
    return "\n".join(lines)


def _build_knowledge_context(hospital_id: str, query: str, top_k: int = 5) -> str:
    """检索知识库并格式化为 LLM 上下文"""
    results = knowledge_service.search(hospital_id, query, top_k=top_k)
    if not results:
        return ""
    lines = []
    for r in results:
        lines.append(f"- [{r.title}] (相关度: {r.score:.2f})")
    return "\n".join(lines)


def _get_knowledge_refs(hospital_id: str, query: str, top_k: int = 5) -> List[dict]:
    """检索知识库并返回结构化引用"""
    results = knowledge_service.search(hospital_id, query, top_k=top_k)
    return [{"entry_id": r.entry_id, "title": r.title} for r in results]


# ---- Chat Flow ----

def process_chat_stream(
    db: Session,
    session: ChatSession,
    user_message: str,
    user_id: int,
) -> Iterator[str]:
    """处理一条用户消息，流式返回 AI 回复 token"""

    # 1. 保存用户消息
    save_message(db, session.id, "user", user_message)

    # 2. 加载报告上下文
    report_context = "用户未关联报告"
    if session.report_id:
        report_context = _load_report_context(db, session.report_id)

    # 3. 知识库检索
    knowledge_context = _build_knowledge_context(session.hospital_id, user_message)

    # 4. 构建消息历史 (最近 N 轮，不含刚保存的用户消息)
    history = get_messages(db, session.id)
    chat_messages = []
    for msg in history[-MAX_HISTORY_ROUNDS * 2:]:
        chat_messages.append({"role": msg.role, "content": msg.content})

    # 5. 构建完整 messages
    system_content = f"{CHAT_SYSTEM_PROMPT}\n\n## 当前报告数据\n{report_context}\n\n## 参考知识库\n{knowledge_context or '无相关知识库条目'}"
    full_messages = [{"role": "system", "content": system_content}] + chat_messages

    # 6. 流式调用 LLM
    full_response = ""
    try:
        for token in llm_client.chat(full_messages, stream=True):
            full_response += token
            yield token
    except Exception:
        yield "__ERROR__:AI 响应失败，请重试"
        return

    # 7. 保存 AI 回复
    refs = _get_knowledge_refs(session.hospital_id, user_message)
    save_message(db, session.id, "assistant", full_response, knowledge_refs=refs)

    # 8. 首条消息自动生成标题
    if not session.title:
        title = user_message[:50] + ("..." if len(user_message) > 50 else "")
        db.query(ChatSession).filter(ChatSession.id == session.id).update({"title": title})
        db.commit()
```

- [ ] **Step 2: 验证 service 可导入**

Run: `cd backend && uv run python -c "from app.modules.chat import service; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/modules/chat/service.py
git commit -m "feat: add chat service with session CRUD, RAG context, streaming LLM call"
```

---

### Task 6: 创建 SSE 流式响应辅助

**Files:**
- Create: `backend/app/modules/chat/stream.py`

- [ ] **Step 1: 创建 stream.py**

```python
import json
from starlette.responses import StreamingResponse


def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_stream(generator):
    """将 token generator 包装为 SSE StreamingResponse"""

    def event_generator():
        for token in generator:
            if token.startswith("__ERROR__:"):
                error_msg = token[len("__ERROR__:"):]
                yield sse_event("error", {"message": error_msg})
                return
            yield sse_event("token", {"content": token})
        yield sse_event("done", {"message_id": None})

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/modules/chat/stream.py
git commit -m "feat: add SSE streaming response helper"
```

---

### Task 7: 创建 Chat Router

**Files:**
- Create: `backend/app/modules/chat/router.py`

- [ ] **Step 1: 创建 router.py**

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.middleware.hospital_context import get_current_hospital_id
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.chat import schemas, service
from app.modules.chat.stream import sse_event, sse_stream

router = APIRouter()


def _get_db(current_user: CurrentUser = Depends(get_current_user)):
    hid = current_user.hospital_id
    if not hid:
        raise ValidationException(detail="Hospital context required")
    return next(get_hospital_db(hid))


# ---- Session CRUD ----

@router.post("/sessions", response_model=schemas.SessionResponse)
def create_session(
    req: schemas.CreateSessionRequest,
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return service.create_session(
        db,
        user_id=current_user.user_id,
        hospital_id=current_user.hospital_id,
        report_id=req.report_id,
    )


@router.get("/sessions", response_model=list[schemas.SessionResponse])
def list_sessions(
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return service.list_sessions(db, user_id=current_user.user_id)


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    if not service.delete_session(db, session_id, user_id=current_user.user_id):
        raise NotFoundException(detail="Session not found")
    return {"status": "deleted"}


# ---- Messages ----

@router.get("/sessions/{session_id}/messages", response_model=list[schemas.MessageResponse])
def get_messages(
    session_id: int,
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    session = service.get_session(db, session_id, user_id=current_user.user_id)
    if not session:
        raise NotFoundException(detail="Session not found")
    return service.get_messages(db, session_id)


@router.post("/sessions/{session_id}/messages")
def send_message(
    session_id: int,
    req: schemas.SendMessageRequest,
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    session = service.get_session(db, session_id, user_id=current_user.user_id)
    if not session:
        raise NotFoundException(detail="Session not found")

    def event_generator():
        full_response = ""
        for token in service.process_chat_stream(db, session, req.content, current_user.user_id):
            if token.startswith("__ERROR__:"):
                error_msg = token[len("__ERROR__:"):]
                yield sse_event("error", {"message": error_msg})
                return
            full_response += token
            yield sse_event("token", {"content": token})
        yield sse_event("done", {"message_id": None})

    from starlette.responses import StreamingResponse
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/modules/chat/router.py
git commit -m "feat: add chat router with session CRUD and SSE streaming endpoints"
```

---

### Task 8: 注册 Chat Router 到 main.py

**Files:**
- Modify: `backend/app/main.py:11-13`

- [ ] **Step 1: 在 main.py 中添加导入和注册**

在 import 区域追加：
```python
from app.modules.chat.router import router as chat_router
```

在 `include_router` 区域追加：
```python
    app.include_router(chat_router, prefix="/api/v1/chat", tags=["chat"])
```

- [ ] **Step 2: 验证路由注册**

Run: `cd backend && uv run python -c "from app.main import app; routes = [r.path for r in app.routes if hasattr(r, 'path')]; print([r for r in routes if 'chat' in r])"`
Expected: 输出包含 `/api/v1/chat/sessions` 等路由

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: register chat router in main app"
```

---

### Task 9: 更新 .env.example 中 chat 相关配置

**Files:**
- Modify: `backend/.env.example`

- [ ] **Step 1: 确认 .env.example 已包含所有 config.py 中的配置项**（已在 Task 1 完成）

- [ ] **Step 2: 跳过（Task 1 已覆盖）**

---

### Task 10: 创建前端 useChatStream Hook

**Files:**
- Create: `frontend/packages/user-portal/src/hooks/useChatStream.ts`

- [ ] **Step 1: 创建目录并创建 hook**

```typescript
import { useCallback, useRef } from 'react';

interface UseChatStreamOptions {
  onToken: (token: string) => void;
  onDone: (result: { messageId?: number; knowledgeRefs?: Array<{ entry_id: number; title: string }> }) => void;
  onError: (error: string) => void;
}

export function useChatStream({ onToken, onDone, onError }: UseChatStreamOptions) {
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(async (url: string, content: string) => {
    abortRef.current = new AbortController();
    const token = localStorage.getItem('token') || '';

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ content }),
        signal: abortRef.current.signal,
      });

      if (!response.ok) {
        onError('请求失败');
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        onError('无法读取响应流');
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            const eventType = line.slice(7).trim();
            continue; // wait for data line
          }
          if (line.startsWith('data: ')) {
            const dataStr = line.slice(6);
            try {
              const data = JSON.parse(dataStr);

              // Determine event type from previous line or data content
              if (data.message && data.message.includes('失败')) {
                onError(data.message);
                return;
              }
              if (data.message_id !== undefined || (data.content === undefined && data.message === undefined)) {
                onDone(data);
                return;
              }
              if (data.content !== undefined) {
                onToken(data.content);
              }
            } catch {
              // skip parse error
            }
          }
        }
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        onError('网络错误，请重试');
      }
    }
  }, [onToken, onDone, onError]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { send, abort };
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit -p packages/user-portal/tsconfig.json 2>&1 | head -20`
Expected: No errors related to useChatStream

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/user-portal/src/hooks/useChatStream.ts
git commit -m "feat: add useChatStream hook for SSE streaming"
```

---

### Task 11: 创建前端 chatStore

**Files:**
- Create: `frontend/packages/user-portal/src/stores/chatStore.ts`

- [ ] **Step 1: 创建 chatStore.ts**

```typescript
import { create } from 'zustand';

interface Message {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  knowledgeRefs?: Array<{ entry_id: number; title: string }>;
  streaming?: boolean;
}

interface ChatSession {
  id: number;
  report_id: number | null;
  title: string | null;
  created_at: string;
  updated_at: string;
}

interface ChatStore {
  sessions: ChatSession[];
  currentSessionId: number | null;
  messages: Message[];
  loading: boolean;
  streaming: boolean;

  setSessions: (sessions: ChatSession[]) => void;
  setCurrentSession: (id: number | null) => void;
  setMessages: (messages: Message[]) => void;
  addMessage: (msg: Message) => void;
  appendToken: (token: string) => void;
  finishStreaming: () => void;
  setLoading: (loading: boolean) => void;
  setStreaming: (streaming: boolean) => void;
  removeLastAssistantMessage: () => void;
}

export const useChatStore = create<ChatStore>((set) => ({
  sessions: [],
  currentSessionId: null,
  messages: [],
  loading: false,
  streaming: false,

  setSessions: (sessions) => set({ sessions }),
  setCurrentSession: (id) => set({ currentSessionId: id }),
  setMessages: (messages) => set({ messages }),
  addMessage: (msg) =>
    set((state) => ({ messages: [...state.messages, msg] })),
  appendToken: (token) =>
    set((state) => {
      const msgs = [...state.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant' && last.streaming) {
        last.content += token;
      }
      return { messages: msgs };
    }),
  finishStreaming: () =>
    set((state) => {
      const msgs = [...state.messages];
      const last = msgs[msgs.length - 1];
      if (last && last.role === 'assistant') {
        last.streaming = false;
      }
      return { messages: msgs, streaming: false };
    }),
  setLoading: (loading) => set({ loading }),
  setStreaming: (streaming) => set({ streaming }),
  removeLastAssistantMessage: () =>
    set((state) => {
      const msgs = [...state.messages];
      if (msgs.length > 0 && msgs[msgs.length - 1].role === 'assistant') {
        msgs.pop();
      }
      return { messages: msgs };
    }),
}));
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit -p packages/user-portal/tsconfig.json 2>&1 | head -20`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/user-portal/src/stores/chatStore.ts
git commit -m "feat: add chatStore with Zustand state management"
```

---

### Task 12: 创建 ChatBubble 组件

**Files:**
- Create: `frontend/packages/user-portal/src/components/ChatBubble.tsx`

- [ ] **Step 1: 创建 ChatBubble.tsx**

```tsx
import { Typography } from 'antd';

interface Props {
  role: 'user' | 'assistant';
  content: string;
  knowledgeRefs?: Array<{ entry_id: number; title: string }>;
  streaming?: boolean;
}

export default function ChatBubble({ role, content, knowledgeRefs, streaming }: Props) {
  const isUser = role === 'user';

  return (
    <div style={{
      display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start',
      marginBottom: 12,
    }}>
      <div style={{
        maxWidth: '80%',
        background: isUser ? '#E5E7EB' : '#CCFBF1',
        borderRadius: 8,
        padding: '8px 12px',
        fontSize: 14,
        lineHeight: 1.6,
        whiteSpace: 'pre-wrap',
      }}>
        <Typography.Text style={{ fontSize: 14 }}>
          {content}
          {streaming && <span style={{
            display: 'inline-block', width: 6, height: 14,
            background: '#0D9488', marginLeft: 2, verticalAlign: 'text-bottom',
            animation: 'blink 1s infinite',
          }} />}
        </Typography.Text>
        {!isUser && knowledgeRefs && knowledgeRefs.length > 0 && (
          <div style={{ marginTop: 8, borderTop: '1px solid #D1FAE5', paddingTop: 6 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              参考：{knowledgeRefs.map(r => r.title).join('、')}
            </Typography.Text>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/user-portal/src/components/ChatBubble.tsx
git commit -m "feat: add ChatBubble component with streaming indicator"
```

---

### Task 13: 创建 ChatInput 组件

**Files:**
- Create: `frontend/packages/user-portal/src/components/ChatInput.tsx`

- [ ] **Step 1: 创建 ChatInput.tsx**

```tsx
import { useState } from 'react';
import { Input, Button } from 'antd';
import { SendOutlined } from '@ant-design/icons';

interface Props {
  onSend: (content: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export default function ChatInput({ onSend, disabled, placeholder }: Props) {
  const [value, setValue] = useState('');

  const handleSend = () => {
    if (!value.trim() || disabled) return;
    onSend(value.trim());
    setValue('');
  };

  return (
    <div style={{ display: 'flex', gap: 8, padding: '8px 0' }}>
      <Input.TextArea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onPressEnter={(e) => {
          if (!e.shiftKey) { e.preventDefault(); handleSend(); }
        }}
        placeholder={placeholder || '输入健康问题...'}
        autoSize={{ minRows: 1, maxRows: 4 }}
        disabled={disabled}
        style={{ flex: 1, borderRadius: 8 }}
      />
      <Button
        type="primary"
        icon={<SendOutlined />}
        onClick={handleSend}
        disabled={disabled || !value.trim()}
        style={{ borderRadius: 8 }}
      />
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/user-portal/src/components/ChatInput.tsx
git commit -m "feat: add ChatInput component"
```

---

### Task 14: 创建 ChatPanel 组件

**Files:**
- Create: `frontend/packages/user-portal/src/components/ChatPanel.tsx`

- [ ] **Step 1: 创建 ChatPanel.tsx**

```tsx
import { useEffect, useRef, useCallback } from 'react';
import { Spin } from 'antd';
import { useUserStore } from '../stores/userStore';
import { useChatStore } from '../stores/chatStore';
import { useChatStream } from '../hooks/useChatStream';
import ChatBubble from './ChatBubble';
import ChatInput from './ChatInput';

interface Props {
  sessionId: number;
  placeholder?: string;
  compact?: boolean;
}

export default function ChatPanel({ sessionId, placeholder, compact }: Props) {
  const { api } = useUserStore();
  const store = useChatStore();
  const bottomRef = useRef<HTMLDivElement>(null);

  const onToken = useCallback((token: string) => {
    store.appendToken(token);
  }, []);

  const onDone = useCallback(() => {
    store.finishStreaming();
  }, []);

  const onError = useCallback((err: string) => {
    store.removeLastAssistantMessage();
    store.finishStreaming();
  }, []);

  const { send, abort } = useChatStream({ onToken, onDone, onError });

  // Load messages when session changes
  useEffect(() => {
    if (!sessionId) return;
    store.setLoading(true);
    api.get(`/chat/sessions/${sessionId}/messages`)
      .then(r => store.setMessages(r.data || []))
      .catch(() => {})
      .finally(() => store.setLoading(false));
  }, [sessionId]);

  // Auto scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [store.messages]);

  const handleSend = (content: string) => {
    store.addMessage({ role: 'user', content });
    store.setStreaming(true);
    store.addMessage({ role: 'assistant', content: '', streaming: true });
    send(
      `${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'}/chat/sessions/${sessionId}/messages`,
      content,
    );
  };

  const maxHeight = compact ? 280 : 'calc(100vh - 200px)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{
        flex: 1, overflowY: 'auto', padding: '0 4px',
        maxHeight, minHeight: 160,
      }}>
        {store.loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}><Spin size="small" /></div>
        ) : store.messages.length === 0 ? (
          <div style={{
            textAlign: 'center', padding: 40,
            color: 'var(--color-text-secondary)', fontSize: 13,
          }}>
            基于您的体检报告，我可以帮您解答健康疑问
          </div>
        ) : (
          store.messages.map((msg, i) => (
            <ChatBubble
              key={i}
              role={msg.role}
              content={msg.content}
              knowledgeRefs={msg.knowledgeRefs}
              streaming={msg.streaming}
            />
          ))
        )}
        <div ref={bottomRef} />
      </div>
      <ChatInput
        onSend={handleSend}
        disabled={store.streaming}
        placeholder={placeholder}
      />
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/user-portal/src/components/ChatPanel.tsx
git commit -m "feat: add ChatPanel component with streaming and auto-scroll"
```

---

### Task 15: 创建 SessionDrawer 组件

**Files:**
- Create: `frontend/packages/user-portal/src/components/SessionDrawer.tsx`

- [ ] **Step 1: 创建 SessionDrawer.tsx**

```tsx
import { useEffect } from 'react';
import { Drawer, List, Button, Typography, Popconfirm } from 'antd';
import { PlusOutlined, DeleteOutlined, MessageOutlined } from '@ant-design/icons';
import { useUserStore } from '../stores/userStore';
import { useChatStore } from '../stores/chatStore';

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function SessionDrawer({ open, onClose }: Props) {
  const { api } = useUserStore();
  const store = useChatStore();

  useEffect(() => {
    if (!open) return;
    api.get('/chat/sessions')
      .then(r => store.setSessions(r.data || []))
      .catch(() => {});
  }, [open]);

  const handleNew = async () => {
    try {
      const r = await api.post('/chat/sessions', {});
      store.setCurrentSession(r.data.id);
      store.setMessages([]);
      onClose();
    } catch {}
  };

  const handleSelect = (id: number) => {
    store.setCurrentSession(id);
    onClose();
  };

  const handleDelete = async (id: number) => {
    try {
      await api.delete(`/chat/sessions/${id}`);
      store.setSessions(store.sessions.filter(s => s.id !== id));
      if (store.currentSessionId === id) {
        store.setCurrentSession(null);
        store.setMessages([]);
      }
    } catch {}
  };

  return (
    <Drawer title="对话历史" open={open} onClose={onClose} width={280}>
      <Button type="primary" icon={<PlusOutlined />} block onClick={handleNew}
        style={{ marginBottom: 16, borderRadius: 8 }}>
        新对话
      </Button>
      <List
        dataSource={store.sessions}
        renderItem={(session) => (
          <List.Item
            onClick={() => handleSelect(session.id)}
            style={{
              cursor: 'pointer', borderRadius: 8, padding: '8px 12px',
              background: session.id === store.currentSessionId ? '#F0FDFA' : undefined,
            }}
            actions={[
              <Popconfirm title="确定删除？" onConfirm={(e) => { e?.stopPropagation(); handleDelete(session.id); }}>
                <DeleteOutlined onClick={(e) => e?.stopPropagation()}
                  style={{ color: '#EF4444', fontSize: 12 }} />
              </Popconfirm>
            ]}
          >
            <List.Item.Meta
              avatar={<MessageOutlined style={{ color: '#0D9488' }} />}
              title={<Typography.Text ellipsis style={{ fontSize: 13 }}>{session.title || '新对话'}</Typography.Text>}
              description={<Typography.Text type="secondary" style={{ fontSize: 11 }}>{session.updated_at?.slice(0, 16)}</Typography.Text>}
            />
          </List.Item>
        )}
      />
    </Drawer>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/user-portal/src/components/SessionDrawer.tsx
git commit -m "feat: add SessionDrawer component for conversation history"
```

---

### Task 16: 创建 ChatPage 独立聊天页面

**Files:**
- Create: `frontend/packages/user-portal/src/pages/ChatPage.tsx`

- [ ] **Step 1: 创建 ChatPage.tsx**

```tsx
import { useState, useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from 'antd';
import { MenuOutlined } from '@ant-design/icons';
import Layout from '../components/Layout';
import ChatPanel from '../components/ChatPanel';
import SessionDrawer from '../components/SessionDrawer';
import { useUserStore } from '../stores/userStore';
import { useChatStore } from '../stores/chatStore';

export default function ChatPage() {
  const { sessionId } = useParams();
  const { api } = useUserStore();
  const store = useChatStore();
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    if (sessionId) {
      store.setCurrentSession(Number(sessionId));
      return;
    }
    // 加载最新会话或创建新会话
    api.get('/chat/sessions')
      .then(r => {
        const sessions = r.data || [];
        if (sessions.length > 0) {
          store.setCurrentSession(sessions[0].id);
          store.setSessions(sessions);
        } else {
          api.post('/chat/sessions', {}).then(r2 => {
            store.setCurrentSession(r2.data.id);
          }).catch(() => {});
        }
      })
      .catch(() => {});
  }, [sessionId]);

  return (
    <Layout title="AI 健康咨询">
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <Button type="text" icon={<MenuOutlined />} onClick={() => setDrawerOpen(true)}
          style={{ color: 'var(--color-text-secondary)' }}>
          历史对话
        </Button>
      </div>

      {store.currentSessionId ? (
        <ChatPanel sessionId={store.currentSessionId} />
      ) : (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--color-text-secondary)' }}>
          加载中...
        </div>
      )}

      <SessionDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </Layout>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/user-portal/src/pages/ChatPage.tsx
git commit -m "feat: add ChatPage with session management"
```

---

### Task 17: 更新前端路由

**Files:**
- Modify: `frontend/packages/user-portal/src/router.tsx`

- [ ] **Step 1: 修改 router.tsx，导入 ChatPage 并添加路由**

在 import 区域追加：
```tsx
import ChatPage from './pages/ChatPage';
```

在 `<Routes>` 中添加两行（在 ProfilePage 路由之前）：
```tsx
    <Route path="/chat" element={<AuthGuard><ChatPage /></AuthGuard>} />
    <Route path="/chat/:sessionId" element={<AuthGuard><ChatPage /></AuthGuard>} />
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/user-portal/src/router.tsx
git commit -m "feat: add /chat routes to user portal"
```

---

### Task 18: 集成 ChatPanel 到报告详情页

**Files:**
- Modify: `frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`

- [ ] **Step 1: 先读取 ReportDetailPage.tsx 了解现有结构**

Run: `cat frontend/packages/user-portal/src/pages/ReportDetailPage.tsx`

- [ ] **Step 2: 在报告详情页底部嵌入 ChatPanel**

在该页面的指标列表/解读内容下方追加：
```tsx
import ChatPanel from '../components/ChatPanel';
import { useChatStore } from '../stores/chatStore';

// 在组件中添加：
const chatStore = useChatStore();
const [chatSessionId, setChatSessionId] = useState<number | null>(null);

useEffect(() => {
  if (!reportId) return;
  // 查找或创建该报告的会话
  api.get('/chat/sessions')
    .then(r => {
      const sessions = r.data || [];
      const existing = sessions.find((s: any) => s.report_id === Number(reportId));
      if (existing) {
        setChatSessionId(existing.id);
        chatStore.setCurrentSession(existing.id);
      } else {
        api.post('/chat/sessions', { report_id: Number(reportId) })
          .then(r2 => {
            setChatSessionId(r2.data.id);
            chatStore.setCurrentSession(r2.data.id);
          }).catch(() => {});
      }
    })
    .catch(() => {});
}, [reportId]);

// 在页面底部（指标表格下方、页面 footer 之前）渲染：
{chatSessionId && (
  <div style={{ marginTop: 24, borderTop: '1px solid #E5E7EB', paddingTop: 16 }}>
    <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14, color: '#0D9488' }}>
      💬 AI 健康咨询（基于本报告）
    </div>
    <ChatPanel sessionId={chatSessionId} placeholder="基于本报告提问..." compact />
  </div>
)}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/user-portal/src/pages/ReportDetailPage.tsx
git commit -m "feat: embed ChatPanel in ReportDetailPage for report-scoped chat"
```

---

### Task 19: 确保知识库 search 可直接被 chat service 调用

**Files:**
- Verify: `backend/app/modules/knowledge/service.py`
- Verify: `backend/app/modules/knowledge/schemas.py`

- [ ] **Step 1: 验证 search 函数的签名可直接被 chat service 调用**

chat service 中调用方式：
```python
from app.modules.knowledge import service as knowledge_service
results = knowledge_service.search(hospital_id, query, top_k=5)
```

确认参数签名一致：`def search(hospital_id: str, query: str, top_k: int = 5, category_ids: Optional[List[int]] = None) -> List[SearchResult]`

Run: `cd backend && uv run python -c "from app.modules.knowledge.service import search; import inspect; print(inspect.signature(search))"`
Expected: `(hospital_id: str, query: str, top_k: int = 5, category_ids: Optional[List[int]] = None) -> List[app.modules.knowledge.schemas.SearchResult]`

- [ ] **Step 2: 无需代码修改，确认即可。Commit（跳过）**

---

### Task 20: Chat Service 错误处理补全

**Files:**
- Modify: `backend/app/modules/chat/service.py`

- [ ] **Step 1: 在 process_chat_stream 中增加并发控制**

在 service.py 顶部追加导入：
```python
import threading
```

在 `process_chat_stream` 函数开头追加会话锁检查：
```python
    # 并发控制：同一会话同时只能有一个活跃请求
    lock_key = f"_chat_lock_{session.id}"
    if hasattr(db, lock_key) and getattr(db, lock_key):
        yield "__ERROR__:正在处理上一条消息，请稍候"
        return
```

改用模块级字典做并发控制（db 不是线程安全的存储位置）：
```python
# 在文件顶部
_session_locks: set[int] = set()

# 在 process_chat_stream 开头：
    if session.id in _session_locks:
        yield "__ERROR__:正在处理上一条消息，请稍候"
        return
    _session_locks.add(session.id)
    try:
        # ... 原有的流式处理逻辑
    finally:
        _session_locks.discard(session.id)
```

- [ ] **Step 2: 在 LLM 异常时确保 lock 释放**（已通过 try/finally 覆盖）

- [ ] **Step 3: Commit**

```bash
git add backend/app/modules/chat/service.py
git commit -m "feat: add concurrent request guard and error recovery to chat service"
```

---

### Task 21: 运行集成验证

**Files:** 无

- [ ] **Step 1: 启动后端并测试会话 CRUD**

```bash
# 1. 确保基础设施运行
cd backend/docker && docker-compose up -d

# 2. 启动后端
cd backend && uv run uvicorn app.main:app --reload --port 8000 &

# 3. 注册用户获取 token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"chattest","password":"123456","role":"user","hospital_id":"H001"}' | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 4. 创建会话
curl -s -X POST http://localhost:8000/api/v1/chat/sessions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"report_id": null}'

# 5. 列出会话
curl -s http://localhost:8000/api/v1/chat/sessions \
  -H "Authorization: Bearer $TOKEN"

# 6. 发送消息测试 SSE
curl -s -N -X POST http://localhost:8000/api/v1/chat/sessions/1/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"你好"}'
```
Expected: 输出 SSE 事件流 `event: token\ndata: {...}`

- [ ] **Step 2: 验证前端编译**

Run: `cd frontend && npx tsc --noEmit -p packages/user-portal/tsconfig.json`
Expected: No errors

- [ ] **Step 3: Commit** (无文件变更，验证成功即完成)
```

---

该计划共 21 个任务，约需 3-4 小时实现。
