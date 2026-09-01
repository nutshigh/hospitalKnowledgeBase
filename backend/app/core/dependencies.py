from fastapi import Depends, Header
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_template_db
from app.core.security import decode_access_token
from app.middleware.hospital_context import set_current_hospital_id
from app.utils.exceptions import UnauthorizedException, ForbiddenException


class CurrentUser:
    def __init__(self, user_id: int, role: str, hospital_id: Optional[str] = None,
                 id_card_suffix: Optional[str] = None, name: Optional[str] = None):
        self.user_id = user_id
        self.role = role
        self.hospital_id = hospital_id
        self.id_card_suffix = id_card_suffix
        self.name = name


async def get_current_user(
    authorization: str = Header(..., description="Bearer <token>"),
    db: Session = Depends(get_template_db),
) -> CurrentUser:
    if not authorization.startswith("Bearer "):
        raise UnauthorizedException(detail="Invalid authorization header")
    token = authorization[7:]
    payload = decode_access_token(token)
    if payload is None:
        raise UnauthorizedException(detail="Invalid or expired token")
    user_id = payload.get("user_id")
    role = payload.get("role")
    hospital_id = payload.get("hospital_id")
    id_card_suffix = payload.get("id_card_suffix")
    name = payload.get("name")
    if not user_id or not role:
        raise UnauthorizedException(detail="Invalid token payload")
    if hospital_id:
        set_current_hospital_id(hospital_id)
    return CurrentUser(user_id=user_id, role=role, hospital_id=hospital_id,
                       id_card_suffix=id_card_suffix, name=name)


def require_role(*roles: str):
    async def dependency(current_user: CurrentUser = Depends(get_current_user)):
        if current_user.role not in roles:
            raise ForbiddenException(detail=f"Requires role: {roles}")
        return current_user
    return dependency


def user_identity(current_user) -> tuple[Optional[str], Optional[str]]:
    """返回 (user_id_anchor, name_anchor)。

    role='user' 用 id_card_suffix + name 双锚定;doctor/admin 用 str(platform user_id)+None
    匹配存量会话/报告;存量 role='user' 无后缀 -> (None, None) 表示无结果(调用方须空/拒绝,不泄露)。
    """
    if current_user.role == "user":
        return current_user.id_card_suffix, current_user.name
    return str(current_user.user_id), None
