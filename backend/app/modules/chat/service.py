from typing import Iterator, List, Optional, AsyncIterator
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.modules.chat.models import ChatSession, ChatMessage
from app.ai.agents import run_chat_agent

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


# ---- Chat Flow ----

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
