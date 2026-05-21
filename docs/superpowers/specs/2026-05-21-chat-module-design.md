# Chat 模块 — AI 对话功能设计

## 概述

在用户端新增 AI 聊天功能，支持**报告内嵌聊天**（基于当前报告上下文）和**独立聊天页面**（自由健康咨询）。AI 结合知识库检索（RAG）+ 用户报告数据进行流式问答，支持多轮对话和会话历史持久化。

---

## 1. 架构位置

Chat 模块作为新的业务模块，位于业务模块层，与知识库模块、报告解析模块平行。

```
业务模块层
├── 知识库模块    (依赖)
├── 报告解析模块
├── AI解读模块
├── 统计分析模块
├── Chat 模块    ← 新增
└── 调度管理模块
```

**依赖关系：**
- 知识库模块 — 调用语义检索 API 获取医学知识上下文
- LLM Client — 策略模式多后端（本地 vLLM / 远端 OpenAI 兼容 API），通过 `LLM_PROVIDER` 配置切换
- MySQL — 新增 chat_session + chat_message 表
- 不依赖 RabbitMQ，Chat 是同步流式交互

---

## 2. 核心数据流

```
用户发送消息
      │
      ▼
┌─────────────────┐
│ 1. 加载上下文     │  ← 根据 session.report_id:
│                  │     有值 → 从 DB 读取该报告结构化指标数据
│                  │     为空 → 可选，用户最近报告摘要
└────────┬────────┘
         ▼
┌─────────────────┐
│ 2. 知识库检索     │  ← 最近 N 轮对话摘要 + 当前消息 作为 query
│                  │     POST /api/v1/knowledge/internal/search
│                  │     返回 Top-5 医学知识
└────────┬────────┘
         ▼
┌─────────────────┐
│ 3. 构建 Prompt   │  ← System: 角色设定 + 报告数据上下文 + 知识检索结果
│                  │     Messages: 近 20 轮历史对话 + 当前用户消息
└────────┬────────┘
         ▼
┌─────────────────┐
│ 4. LLM 流式生成  │  ← 调用 LLM (本地 vLLM / 远端 API, stream=true)
│                  │     SSE 逐 token 推送给前端
└────────┬────────┘
         ▼
┌─────────────────┐
│ 5. 保存对话记录   │  ← 用户消息 + AI 完整回复写入 chat_message 表
│                  │     首次对话自动用首条消息生成会话标题
└─────────────────┘
```

---

## 3. 数据库设计

### chat_session — 会话表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| user_id | BIGINT | 所属用户 |
| hospital_id | VARCHAR(32) | 所属医院 |
| report_id | BIGINT (可空) | 关联报告，NULL 为独立聊天 |
| title | VARCHAR(200) | 会话标题（首条消息截取） |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 最后活跃时间 |

### chat_message — 消息表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT PK | 主键 |
| session_id | BIGINT FK | 关联 chat_session.id |
| role | VARCHAR(10) | user / assistant |
| content | TEXT | 消息内容 |
| knowledge_refs | JSON (可空) | 引用的知识条目 [{entry_id, title}] |
| created_at | DATETIME | 发送时间 |

---

## 4. API 接口设计

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/v1/chat/sessions | 创建会话 (body: {report_id?: number}) |
| GET | /api/v1/chat/sessions | 会话列表 |
| GET | /api/v1/chat/sessions/{id}/messages | 获取历史消息 |
| POST | /api/v1/chat/sessions/{id}/messages | 发送消息 (SSE 流式响应) |
| DELETE | /api/v1/chat/sessions/{id} | 删除会话 |

### SSE 流式响应格式

请求：
```json
POST /api/v1/chat/sessions/{id}/messages
{"content": "我的空腹血糖8.2，这个严重吗？"}
```

响应：
```
event: token
data: {"content": "您"}

event: token
data: {"content": "的"}

event: token
data: {"content": "空腹"}

...

event: done
data: {"message_id": 42, "knowledge_refs": [{"entry_id": 5, "title": "血糖标准"}]}

event: error
data: {"message": "AI 响应失败，请重试"}
```

---

## 5. LLM Client 多后端改造

现有 `llm_client` 仅支持本地 vLLM 部署。改造为**策略模式**，支持本地/远端双后端，通过配置切换。

### 5.1 架构

```
                    ┌─────────────────────┐
                    │    LLMClient (门面)   │
                    │  ─ provider: LLMProvider
                    │  ─ chat(messages, stream)
                    │  ─ chat_stream(messages)
                    └──────────┬──────────┘
                               │
                    ┌──────────┴──────────┐
                    │                     │
            ┌───────┴───────┐   ┌────────┴────────┐
            │ VLLMProvider   │   │ RemoteProvider    │
            │ (vLLM API)     │   │ (OpenAI 兼容 API) │
            │ localhost:8000 │   │ 可切换任意远端模型  │
            └───────────────┘   └─────────────────┘
```

### 5.2 配置项 (.env)

```bash
# === 后端选择 ===
LLM_PROVIDER=local          # local | remote

# === 本地 vLLM ===
VLLM_BASE_URL=http://localhost:8000/v1
VLLM_CHAT_MODEL=qwen2.5-7b

# === 远端 API (OpenAI 兼容) ===
REMOTE_LLM_BASE_URL=https://api.deepseek.com/v1
REMOTE_LLM_API_KEY=sk-xxxxxxxx
REMOTE_LLM_MODEL=deepseek-chat       # 可通过改配置切换为其他模型
REMOTE_LLM_MAX_TOKENS=4096
REMOTE_LLM_TEMPERATURE=0.1

# === 通用 ===
LLM_TIMEOUT_SECONDS=120
```

### 5.3 切换方式

- 修改 `.env` 中的 `LLM_PROVIDER` 值，重启后端即生效
- 远端模型切换：修改 `REMOTE_LLM_MODEL` 值即可（如 `deepseek-chat` → `gpt-4o` → `qwen-max`），只要是 OpenAI 兼容 API 都能直接适配
- 后续可在医生端管理后台增加可视化的模型切换 UI（本阶段不包含）

### 5.4 接口定义

```python
from abc import ABC, abstractmethod
from typing import Iterator

class LLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict], temperature: float, max_tokens: int) -> str: ...
    
    @abstractmethod
    def chat_stream(self, messages: list[dict], temperature: float, max_tokens: int) -> Iterator[str]: ...

class VLLMProvider(LLMProvider):
    # vLLM OpenAI 兼容 API 实现 (http://host:8000/v1/chat/completions)
    # 本地部署模型，增加 stream 模式

class RemoteProvider(LLMProvider):
    # OpenAI 兼容实现 (Bearer Token 认证)
    # base_url / api_key / model 均从配置读取

class LLMClient:
    def __init__(self):
        self._provider = self._create_provider()
    
    def _create_provider(self) -> LLMProvider:
        if settings.LLM_PROVIDER == "remote":
            return RemoteProvider(...)
        return VLLMProvider(...)
    
    def chat(self, messages, stream=False, temperature=None, max_tokens=None) -> str | Iterator[str]:
        # 委托给 provider
```

### 5.5 Chat 模块调用方式

Chat 模块**不感知**底层用的是哪个 Provider，统一调用 `llm_client.chat(messages, stream=True)`。后端切换对 Chat 模块透明。

---

## 6. Prompt 结构

### System Prompt

```
你是专业的体检报告解读医生助手。结合提供的医学知识库和体检数据，为体检者提供易懂的健康咨询。

规则:
1. 基于报告数据和知识库回答，不编造信息
2. 引用知识库内容时注明来源
3. 建议具体可执行，避免笼统
4. 不诊断疾病，只做健康风险提示
5. 危急值指标提示"建议立即就医复查"
6. 用户未关联报告时，引导其先上传报告以获取更精准建议

## 当前报告数据
{report_context 或 "用户未关联报告"}

## 参考知识库
{knowledge_context 或 "无相关知识库条目"}
```

### 上下文窗口管理

- 保留最近 20 轮历史消息
- 超过 20 轮时，对早期对话做摘要压缩
- 知识检索结果每次实时查询，不缓存
- 总 token 数控制在模型限制内（本地模型通常 4K-8K）

---

## 7. 前端设计

### 路由

| 路由 | 组件 | 说明 |
|------|------|------|
| /chat | ChatPage | 独立聊天页（默认最新会话） |
| /chat/:sessionId | ChatPage | 指定会话 |

### 组件树

```
ChatPage
├── SessionDrawer          ← 左侧/抽屉历史会话列表
└── ChatPanel
    ├── ChatBubble[]        ← 消息列表（支持流式逐字渲染）
    │   ├── user bubble
    │   └── assistant bubble (含知识引用标记)
    └── ChatInput           ← 输入框 + 发送按钮
```

### 报告详情页集成

在 ReportDetailPage 底部嵌入 `ChatPanel`：
- 报告详情页首次进入时，自动创建/获取该报告关联的会话
- ChatPanel 底部的 placeholder 改为「基于本报告提问...」

### 状态管理

- `chatStore` — 会话列表、当前会话 ID、消息列表
- `useChatStream` hook — SSE EventSource 管理、逐 token 追加、完成处理

### 流式渲染策略

- 发送消息后立即在消息列表尾部追加空的 assistant bubble
- 每收到一个 token，追加到该 bubble 的 content
- 收到 done 事件时标记完成，保存 knowledge_refs
- 收到 error 事件时显示错误提示，移除该 bubble

---

## 8. 错误处理

| 场景 | 处理方式 |
|------|----------|
| LLM 超时/报错 | SSE 推 error 事件，前端显示"AI 响应失败，请重试"，不入库 |
| 知识库检索无结果 | 不带知识上下文调 LLM，记录 knowledge_miss 日志 |
| SSE 连接中断 | EventSource 自动重连，重连时检查消息是否已入库 |
| 无关联报告 | System prompt 引导用户先上传报告 |
| 对话超长 | 保留最近 20 轮 + 早期摘要压缩 |
| 并发请求 | 同一会话限制 1 个活跃请求，新请求返回 429 |
| 数据库故障 | 返回 503，提示稍后重试 |

---

## 9. 后端文件结构

```
backend/app/modules/chat/
├── __init__.py
├── models.py          # SQLAlchemy: ChatSession, ChatMessage
├── schemas.py         # Pydantic: 请求/响应模型
├── service.py         # 业务逻辑：会话管理 + 消息处理
├── stream.py          # SSE 流式响应辅助
└── router.py          # FastAPI 路由
```

---

## 10. 测试计划

| 层级 | 内容 |
|------|------|
| 单元测试 | service 层：会话创建/消息存储/上下文构建/Prompt 拼装 |
| 集成测试 | SSE 端点流式响应、知识库检索集成、会话 CRUD 生命周期 |
| E2E | 报告详情聊天、独立聊天多轮对话、流式逐字输出、历史会话切换 |

---

## 11. 后续优化方向（不在本阶段）

- **方案 C 混合检索**：语义检索 + 关键词检索 + 报告精确匹配，重排序
- Agent 工具调用模式：LLM 自主决策检索时机和内容
- 语音输入
- 报告图片直接上传到聊天中提问
