import re
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.core.database import get_template_db
from app.core.security import hash_password, verify_password, create_access_token
from app.core.dependencies import get_current_user, CurrentUser
from app.core.hospital_resolver import resolve_hospital, ResolverUnavailableError
from app.utils.exceptions import (
    UnauthorizedException, ValidationException, ServiceUnavailableException,
)

router = APIRouter()

_SUFFIX_RE = re.compile(r"^[0-9]{5}[0-9X]$")


def _valid_suffix(s: str) -> bool:
    return bool(_SUFFIX_RE.match(s))


class AppLoginRequest(BaseModel):
    app_key: str
    name: str
    id_card_suffix: str


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str
    hospital_id: str | None = None
    id_card_suffix: str | None = None
    name: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    role: str
    hospital_id: str | None = None
    id_card_suffix: str | None = None
    name: str | None = None


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_template_db)):
    row = db.execute(
        text("SELECT id, password_hash, role, hospital_id, id_card_suffix, name "
             "FROM platform_user WHERE username = :un AND is_active = 1"),
        {"un": req.username},
    ).fetchone()

    if not row or not verify_password(req.password, row.password_hash):
        raise UnauthorizedException(detail="Invalid username or password")

    token = create_access_token(data={
        "user_id": row.id,
        "role": row.role,
        "hospital_id": row.hospital_id,
        "id_card_suffix": row.id_card_suffix,
        "name": row.name,
    })
    return TokenResponse(
        access_token=token,
        user_id=row.id,
        role=row.role,
        hospital_id=row.hospital_id,
        id_card_suffix=row.id_card_suffix,
        name=row.name,
    )


@router.post("/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_template_db)):
    if req.role not in ("user", "doctor", "admin"):
        raise ValidationException(detail="Invalid role")

    if req.role == "user":
        if not req.hospital_id:
            raise ValidationException(detail="hospital_id required for role=user")
        if not req.name:
            raise ValidationException(detail="name required for role=user")
        if not req.id_card_suffix or not _valid_suffix(req.id_card_suffix):
            raise ValidationException(
                detail="id_card_suffix required (5 digits + digit or X) for role=user")

    existing = db.execute(
        text("SELECT id FROM platform_user WHERE username = :un"), {"un": req.username}
    ).fetchone()
    if existing:
        raise ValidationException(detail="Username already exists")

    dup = db.execute(
        text("SELECT id FROM platform_user "
             "WHERE hospital_id = :hid AND name = :name AND id_card_suffix = :suf"),
        {"hid": req.hospital_id, "name": req.name, "suf": req.id_card_suffix},
    ).fetchone()
    if dup:
        raise ValidationException(
            detail="User with same hospital_id + name + id_card_suffix already exists")

    db.execute(
        text("INSERT INTO platform_user "
             "(username, password_hash, role, hospital_id, id_card_suffix, name) "
             "VALUES (:un, :ph, :r, :hid, :suf, :name)"),
        {"un": req.username, "ph": hash_password(req.password),
         "r": req.role, "hid": req.hospital_id, "suf": req.id_card_suffix,
         "name": req.name},
    )
    db.commit()

    row = db.execute(
        text("SELECT id, role, hospital_id, id_card_suffix, name "
             "FROM platform_user WHERE username = :un"),
        {"un": req.username},
    ).fetchone()

    token = create_access_token(data={
        "user_id": row.id,
        "role": row.role,
        "hospital_id": row.hospital_id,
        "id_card_suffix": row.id_card_suffix,
        "name": row.name,
    })
    return TokenResponse(
        access_token=token,
        user_id=row.id,
        role=row.role,
        hospital_id=row.hospital_id,
        id_card_suffix=row.id_card_suffix,
        name=row.name,
    )


@router.get("/me", response_model=TokenResponse)
def me(current_user: CurrentUser = Depends(get_current_user)):
    return TokenResponse(
        access_token="",
        user_id=current_user.user_id,
        role=current_user.role,
        hospital_id=current_user.hospital_id,
        id_card_suffix=current_user.id_card_suffix,
        name=current_user.name,
    )


@router.post("/app-login", response_model=TokenResponse)
def app_login(req: AppLoginRequest, db: Session = Depends(get_template_db)):
    if not settings.APP_API_KEY or not secrets.compare_digest(
        req.app_key.encode(), settings.APP_API_KEY.encode()
    ):
        raise UnauthorizedException(detail="Invalid app key")

    name = req.name.strip()
    if not name:
        raise ValidationException(detail="name required")
    if len(name) > 50:
        raise ValidationException(detail="name too long (max 50)")
    if not req.id_card_suffix or not _valid_suffix(req.id_card_suffix):
        raise ValidationException(detail="id_card_suffix required (5 digits + digit or X)")

    try:
        hospital_id = resolve_hospital(req.id_card_suffix)
    except ResolverUnavailableError as e:
        raise ServiceUnavailableException(detail="resolver 不可用") from e
    if not hospital_id:
        raise UnauthorizedException(detail="无法匹配用户医院")

    row = db.execute(
        text("SELECT id, role, hospital_id, id_card_suffix, name, is_active "
             "FROM platform_user "
             "WHERE hospital_id = :hid AND name = :name AND id_card_suffix = :suf"),
        {"hid": hospital_id, "name": name, "suf": req.id_card_suffix},
    ).fetchone()
    if row is not None and not row.is_active:
        raise UnauthorizedException(detail="用户已停用")
    if row is None:
        row = _auto_register(db, hospital_id, name, req.id_card_suffix)

    token = create_access_token(
        data={"user_id": row.id, "role": row.role, "hospital_id": row.hospital_id,
              "id_card_suffix": row.id_card_suffix, "name": row.name},
        expires_delta=timedelta(minutes=settings.APP_LOGIN_TOKEN_EXPIRE_MINUTES),
    )
    return TokenResponse(
        access_token=token, token_type="bearer", user_id=row.id, role=row.role,
        hospital_id=row.hospital_id, id_card_suffix=row.id_card_suffix, name=row.name,
    )


def _auto_register(db, hospital_id: str, name: str, id_card_suffix: str):
    """三元组不存在时自动注册;并发撞唯一索引时回查已有行(幂等)。"""
    username = f"app_{hospital_id}_{name}_{id_card_suffix}"
    password_hash = hash_password(secrets.token_urlsafe(32))
    try:
        db.execute(
            text("INSERT INTO platform_user "
                 "(username, password_hash, role, hospital_id, id_card_suffix, name) "
                 "VALUES (:un, :ph, :r, :hid, :suf, :name)"),
            {"un": username, "ph": password_hash, "r": "user", "hid": hospital_id,
             "suf": id_card_suffix, "name": name},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
    row = db.execute(
        text("SELECT id, role, hospital_id, id_card_suffix, name, is_active "
             "FROM platform_user "
             "WHERE hospital_id = :hid AND name = :name AND id_card_suffix = :suf"),
        {"hid": hospital_id, "name": name, "suf": id_card_suffix},
    ).fetchone()
    if row is None:
        raise ServiceUnavailableException(detail="用户注册失败,请重试")
    return row
