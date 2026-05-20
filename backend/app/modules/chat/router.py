from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.chat import schemas, service
from app.modules.chat.stream import sse_event

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
        for token in service.process_chat_stream(db, session, req.content, current_user.user_id):
            if token.startswith("__ERROR__:"):
                error_msg = token[len("__ERROR__:"):]
                yield sse_event("error", {"message": error_msg})
                return
            yield sse_event("token", {"content": token})
        yield sse_event("done", {"message_id": None})

    from starlette.responses import StreamingResponse
    return StreamingResponse(event_generator(), media_type="text/event-stream")
