from fastapi import Depends, Header
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_template_db
from app.core.security import decode_access_token
from app.middleware.hospital_context import set_current_hospital_id
from app.utils.exceptions import UnauthorizedException, ForbiddenException


class CurrentUser:
    def __init__(self, user_id: int, role: str, hospital_id: Optional[str] = None):
        self.user_id = user_id
        self.role = role
        self.hospital_id = hospital_id


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
    if not user_id or not role:
        raise UnauthorizedException(detail="Invalid token payload")
    if hospital_id:
        set_current_hospital_id(hospital_id)
    return CurrentUser(user_id=user_id, role=role, hospital_id=hospital_id)


def require_role(*roles: str):
    async def dependency(current_user: CurrentUser = Depends(get_current_user)):
        if current_user.role not in roles:
            raise ForbiddenException(detail=f"Requires role: {roles}")
        return current_user
    return dependency
