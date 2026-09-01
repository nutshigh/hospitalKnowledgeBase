import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_template_db
from app.core.security import hash_password, verify_password, create_access_token
from app.core.dependencies import get_current_user, CurrentUser
from app.utils.exceptions import UnauthorizedException, ValidationException

router = APIRouter()

_SUFFIX_RE = re.compile(r"^[0-9]{5}[0-9X]$")


def _valid_suffix(s: str) -> bool:
    return bool(_SUFFIX_RE.match(s))


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
