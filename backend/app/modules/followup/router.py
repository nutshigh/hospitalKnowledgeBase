from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_hospital_db, get_template_db
from app.core.dependencies import (get_current_user, CurrentUser,
                                   user_identity, require_role)
from app.utils.exceptions import NotFoundException, ValidationException
from app.modules.followup import service, schemas

followup_router = APIRouter()
notification_router = APIRouter()


def _get_db(current_user: CurrentUser = Depends(get_current_user)):
    if not current_user.hospital_id:
        raise ValidationException(detail="Hospital context required")
    gen = get_hospital_db(current_user.hospital_id)
    db = next(gen)
    try:
        yield db
    finally:
        gen.close()


def _user_anchor(current_user) -> tuple[Optional[str], Optional[str]]:
    uid, nm = user_identity(current_user)
    return uid, nm


# --------------------------------------------------------------------------
# 平台管理员:激活模板读 / 写(模板库,role=admin)
# --------------------------------------------------------------------------
@followup_router.get("/template")
def get_template(db: Session = Depends(get_template_db),
                 current_user: CurrentUser = Depends(require_role("admin"))):
    return service.get_active_template(db)


@followup_router.put("/template")
def put_template(payload: schemas.TemplateSaveRequest,
                 db: Session = Depends(get_template_db),
                 current_user: CurrentUser = Depends(require_role("admin"))):
    questions = [q.model_dump() for q in payload.questions]
    return service.save_active_template(
        db, payload.name, payload.description, questions,
        updater_id=current_user.user_id)


# --------------------------------------------------------------------------
# 用户侧(role=user,双锚定;app-login JWT 直接可用)
# --------------------------------------------------------------------------
@followup_router.get("/center")
def my_center(page: int = Query(1, ge=1),
              page_size: int = Query(20, ge=1, le=100),
              db: Session = Depends(_get_db),
              current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        return {"items": [], "total": 0, "page": page,
                "page_size": page_size, "has_pending": False}
    return service.list_my_followups(db, uid, nm, page, page_size)


@followup_router.post("/{followup_id}/submit")
def submit(followup_id: int, payload: schemas.SubmitRequest,
           db: Session = Depends(_get_db),
           current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        raise NotFoundException(detail="随访问卷不存在")
    answers = [{"question_id": a.question_id, "answer": a.answer}
               for a in payload.answers]
    return service.submit_followup(db, uid, nm, followup_id, answers)


@followup_router.get("/{followup_id}")
def detail(followup_id: int,
           db: Session = Depends(_get_db),
           current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        raise NotFoundException(detail="随访问卷不存在")
    data = service.get_followup_detail(db, uid, nm, followup_id)
    if not data:
        raise NotFoundException(detail="随访问卷不存在")
    return data


# --------------------------------------------------------------------------
# 医生侧:按报告查看(doctor/admin)
# --------------------------------------------------------------------------
@followup_router.get("/by-report/{report_id}")
def by_report(report_id: int, db: Session = Depends(_get_db),
              current_user: CurrentUser = Depends(require_role("doctor", "admin"))):
    data = service.get_followup_by_report(db, report_id)
    if not data:
        raise NotFoundException(detail="该报告没有随访记录")
    return data


# --------------------------------------------------------------------------
# 通知(role=user)
# --------------------------------------------------------------------------
@notification_router.get("")
def notifications(page: int = Query(1, ge=1),
                  page_size: int = Query(20, ge=1, le=100),
                  unread_only: bool = Query(False),
                  db: Session = Depends(_get_db),
                  current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    return service.list_my_notifications(db, uid, nm, page, page_size, unread_only)


@notification_router.get("/unread-count")
def unread_count(db: Session = Depends(_get_db),
                 current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        return {"unread_count": 0}
    return {"unread_count": service.count_unread(db, uid, nm)}


@notification_router.post("/{notification_id}/read")
def mark_read(notification_id: int, db: Session = Depends(_get_db),
              current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None or not service.mark_notification_read(db, uid, nm, notification_id):
        raise NotFoundException(detail="通知不存在")
    return {"status": "ok"}


@notification_router.post("/read-all")
def mark_all_read(db: Session = Depends(_get_db),
                  current_user: CurrentUser = Depends(require_role("user"))):
    uid, nm = _user_anchor(current_user)
    if uid is None:
        return {"status": "ok"}
    service.mark_all_notifications_read(db, uid, nm)
    return {"status": "ok"}
