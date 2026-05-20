from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.middleware.hospital_context import get_current_hospital_id
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


def _get_hospital_id() -> str:
    hid = get_current_hospital_id()
    if not hid:
        raise ValidationException(detail="Hospital context required")
    return hid


def _get_db(hospital_id: str = Depends(_get_hospital_id)):
    return next(get_hospital_db(hospital_id))


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
    hospital_id: str = Depends(_get_hospital_id),
    current_user: CurrentUser = Depends(get_current_user),
):
    return service.create_session(db, current_user.user_id, hospital_id, data.report_id)


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
def send_message(
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
    return sse_stream(token_gen)
