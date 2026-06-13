from typing import Iterator, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.chat.models import ChatSession, ChatMessage
from app.modules.knowledge import service as knowledge_service
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

_session_locks: set[int] = set()


# ---- Session CRUD ----

def create_session(db: Session, user_id: int, hospital_id: str,
                   report_id: Optional[int] = None) -> ChatSession:
    # If no report specified, auto-associate the user's latest report
    if report_id is None:
        latest = _get_latest_report(db, user_id)
        if latest:
            report_id = latest["id"]
    session = ChatSession(user_id=user_id, hospital_id=hospital_id, report_id=report_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def update_session_report(db: Session, session_id: int, user_id: int,
                          report_id: Optional[int]) -> Optional[ChatSession]:
    session = get_session(db, session_id, user_id)
    if not session:
        return None
    session.report_id = report_id
    db.commit()
    db.refresh(session)
    return session


def _get_report_date_note(db: Session, report_id: int) -> str:
    """获取报告日期标注，用于告知 AI 和用户当前引用的报告"""
    row = db.execute(
        text("SELECT report_date, created_at FROM report_info WHERE id = :rid"),
        {"rid": report_id},
    ).fetchone()
    if not row:
        return ""
    date_str = row[0] or row[1]
    if hasattr(date_str, 'strftime'):
        date_str = date_str.strftime("%Y-%m-%d")
    return f"（当前引用的报告日期：{date_str}，请在你的回答中提及此日期以便用户知晓数据来源）"


def _get_latest_report(db: Session, user_id: int) -> Optional[dict]:
    row = db.execute(
        text(
            "SELECT id, report_date, created_at FROM report_info "
            "WHERE user_id = :uid ORDER BY COALESCE(report_date, created_at) DESC LIMIT 1"
        ),
        {"uid": user_id},
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "report_date": row[1], "created_at": row[2]}


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
    """检索知识库并格式化为 LLM 上下文（降级: embedding 不可用时跳过 RAG）"""
    try:
        results = knowledge_service.search(hospital_id, query, top_k=top_k)
    except Exception:
        return ""
    if not results:
        return ""
    lines = []
    for r in results:
        lines.append(f"- [{r.title}] (相关度: {r.score:.2f})")
    return "\n".join(lines)


def _get_knowledge_refs(hospital_id: str, query: str, top_k: int = 5) -> List[dict]:
    """检索知识库并返回结构化引用（降级: embedding 不可用时返回空）"""
    try:
        results = knowledge_service.search(hospital_id, query, top_k=top_k)
        return [{"entry_id": r.entry_id, "title": r.title} for r in results]
    except Exception:
        return []


# ---- Chat Flow ----

def process_chat_stream(
    db: Session,
    session: ChatSession,
    user_message: str,
    user_id: int,
) -> Iterator[str]:
    """处理一条用户消息，流式返回 AI 回复 token"""

    # 并发控制
    if session.id in _session_locks:
        yield "__ERROR__:正在处理上一条消息，请稍候"
        return
    _session_locks.add(session.id)

    try:
        # 1. 保存用户消息
        save_message(db, session.id, "user", user_message)

        # 2. 加载报告上下文
        report_context = "用户未关联报告"
        report_date_note = ""
        if session.report_id:
            report_context = _load_report_context(db, session.report_id)
            report_date_note = _get_report_date_note(db, session.report_id)

        # 3. 知识库检索
        knowledge_context = _build_knowledge_context(session.hospital_id, user_message)

        # 4. 构建消息历史
        history = get_messages(db, session.id)
        chat_messages = []
        for msg in history[-MAX_HISTORY_ROUNDS * 2:]:
            chat_messages.append({"role": msg.role, "content": msg.content})

        # 5. 构建完整 messages
        system_content = f"{CHAT_SYSTEM_PROMPT}\n\n## 当前报告数据{report_date_note}\n{report_context}\n\n## 参考知识库\n{knowledge_context or '无相关知识库条目'}"
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
    finally:
        _session_locks.discard(session.id)
