from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.chat import service
from app.modules.chat.stream import sse_stream
from app.modules.chat.schemas import (
    CreateSessionRequest,
    SessionResponse,
    SendMessageRequest,
    MessageResponse,
)

router = APIRouter()


def _get_db(
    current_user: CurrentUser = Depends(get_current_user),
):
    if not current_user.hospital_id:
        raise ValidationException(detail="Hospital context required")
    gen = get_hospital_db(current_user.hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return service.list_sessions(db, current_user.user_id)


@router.post("/sessions", response_model=SessionResponse)
def create_session(
    data: CreateSessionRequest,
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return service.create_session(db, current_user.user_id, current_user.hospital_id, data.report_id)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: int,
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    session = service.get_session(db, session_id, current_user.user_id)
    if not session:
        raise NotFoundException(detail="Session not found")
    return session


@router.patch("/sessions/{session_id}")
def update_session(
    session_id: int,
    data: "CreateSessionRequest",
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    session = service.update_session_report(
        db, session_id, current_user.user_id, data.report_id
    )
    if not session:
        raise NotFoundException(detail="Session not found")
    return {"status": "ok", "report_id": session.report_id}


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    if not service.delete_session(db, session_id, current_user.user_id):
        raise NotFoundException(detail="Session not found")
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/messages", response_model=list[MessageResponse])
def get_messages(
    session_id: int,
    db: Session = Depends(_get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    session = service.get_session(db, session_id, current_user.user_id)
    if not session:
        raise NotFoundException(detail="Session not found")
    return service.get_messages(db, session_id)


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
