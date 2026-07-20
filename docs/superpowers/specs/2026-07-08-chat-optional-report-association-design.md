# AI健康咨询会话：体检报告关联改为可选

**Date:** 2026-07-08
**Status:** Approved (Approach A)
**Scope:** 用户门户 → 「AI健康咨询」聊天页（ChatPage）

## 背景

「AI健康咨询」聊天页新建会话时，后端会自动把会话关联到用户最新一份体检报告
（`chat/service.py:create_session` 在 `report_id is None` 时调用
`_get_latest_report` 自动绑定）。用户进入聊天页即被默认绑定到某份报告，
虽然顶部 `ReportSelector` 带 `allowClear` 可手动清除，但整体体验上接近
"必须关联"，与产品诉求"用户自己选择是否关联体检报告"不符。

## 目标

- 新建聊天会话时**不自动关联**报告；由用户通过 `ReportSelector` 主动选择或保持不关联。
- 保留 `ReportDetailPage` 内嵌聊天「基于本报告咨询」的原有语义（仍然在进入时绑定到该报告）。
- 未关联报告的会话仍可正常聊天，AI 据已有 system prompt 规则引导上传报告。

## 现状与影响分析

### 后端调用链
- `POST /api/v1/chat/sessions`（`chat/router.py:create_session`）→
  `chat/service.py:create_session(db, user_id, hospital_id, report_id)`
- 当前 `report_id is None` 时自动取最新报告并绑定。
- 调用方：
  1. `ChatPage` 首次加载无会话时：`api.post('/chat/sessions', {})`（**期望不绑定**）
  2. `SessionDrawer.handleNew`：`api.post('/chat/sessions', {})`（**期望不绑定**）
  3. `ReportDetailPage`：`api.post('/chat/sessions', { report_id: Number(id) })`（不受影响，仍显式绑定）

### 工具降级
- `get_report_indicators` / `get_report_summary` 已对 `report_id is None` 返回
  `{"error": "当前会话未关联报告，请先上传或选择报告"}`，不会被破坏。
- `get_user_history_reports` / `get_indicator_history` 只依赖 `user_id`，不受影响。
- `CHAT_SYSTEM_PROMPT` 第 6 条已指示"用户未关联报告时，引导其先上传报告"。

### 前端
- `ChatPanel` 空状态文案固定为「基于您的体检报告，我可以帮您解答健康疑问」，
  对未关联场景语义不准确，需要调整。
- `ReportSelector` 已支持 `allowClear` 并可 patch 回 `report_id: null`，无需改动逻辑。
- `chatStore.selectedReports` 已支持 `null`，无需改动。

## 设计

### 后端改动

**`backend/app/modules/chat/service.py`**

`create_session` 移除自动关联分支：

```python
def create_session(db: Session, user_id: int, hospital_id: str,
                   report_id: Optional[int] = None) -> ChatSession:
    session = ChatSession(user_id=user_id, hospital_id=hospital_id, report_id=report_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session
```

- 删除 `_get_latest_report` 辅助函数（无人再调用）。
- `update_session_report` 不变（用户仍可通过 patch 主动切换/清除）。

### 前端改动

**`frontend/packages/user-portal/src/components/ChatPanel.tsx`**

把空状态提示从硬编码改为根据当前会话是否关联报告动态显示：

- 未关联：`"您可以关联体检报告以获取更精准解读，或直接向我咨询健康问题"`
- 已关联：保持原文（或弱化措辞）

实现方式：`ChatPanel` 已有 `useChatStore`，可读取
`store.getSelectedReport(sessionId)` 判断是否关联。

> 注：`ChatPanel` 当前从 `useChatStore` 拿 state，但 `selectedReports` 是在
> `ReportSelector` 加载会话时同步进来的；`ChatPage` 用 `ChatPanel`，
> `ReportDetailPage` 也有 `compact` 模式下的 `ChatPanel`。要确保两侧文案都正确：
> `ReportDetailPage` 的会话创建路径会传 `report_id`，正常会被 `ReportSelector`
> 同步为非 null，走"已关联"分支；为稳妥起见，文案分支也容错——查不到状态时默认按
> "未关联"措辞展示。

**`ChatPage.tsx`、`SessionDrawer.tsx`**：新建会话时传参不变（仍 `{}`），
依赖后端不再自动关联。

## 非目标

- 不改动 `ReportDetailPage` 的会话自动创建（仍然绑定到当前报告，属用户主动进入该报告的语义）。
- 不新增"是否关联"开关等 UI 控件（已有 `ReportSelector` + `allowClear` 足够）。
- 不调整 `CHAT_SYSTEM_PROMPT`、`tools` 等已有未关联降级逻辑。

## 测试

### 后端
- `create_session` 显式传 `report_id` → 绑定成功。
- `create_session` 传 `None` → `report_id` 为 `None`，**不再自动取最新报告**。
- 已有用例：`report_id` 仍是 `Optional`，序列化不变。

### 前端
- 「AI健康咨询」首次进入（无会话）→ 新会话 `report_id` 为 `null`，顶部
  `ReportSelector` 显示 placeholder「选择体检报告」；`ChatPanel` 空态文案为未关联版本。
- 通过 `ReportSelector` 选某份报告 → 顶部显示该报告，文案切换为已关联版本。
- 进入「报告详情页」内嵌 chat → 仍自动绑定到该报告，文案为已关联版本。

## 风险

- 老用户历史会话：历史 `ChatSession` 行的 `report_id` 已存库，不受本次改动影响。
- 三色分级统计等依赖报告的展示模块**不**走 chat 会话，不受影响。