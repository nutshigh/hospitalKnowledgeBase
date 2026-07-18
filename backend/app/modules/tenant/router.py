from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.config import settings
from app.core.database import get_template_db
from app.modules.tenant import schemas, service
from app.utils.exceptions import UnauthorizedException

router = APIRouter()


def _require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if settings.ADMIN_TOKEN and x_admin_token != settings.ADMIN_TOKEN:
        raise UnauthorizedException(detail="Invalid admin token")


@router.post("", response_model=schemas.TenantCreateResponse)
def create_tenant(
    req: schemas.TenantCreateRequest,
    _admin: None = Depends(_require_admin),
    db: Session = Depends(get_template_db),
):
    return service.create_tenant(req, db)