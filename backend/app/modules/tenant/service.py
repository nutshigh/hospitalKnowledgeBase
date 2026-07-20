import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.tenant.schemas import TenantCreateRequest, TenantCreateResponse

logger = logging.getLogger("tenant")


def create_tenant(req: TenantCreateRequest, template_db: Session) -> TenantCreateResponse:
    existing = template_db.execute(
        text(
            "SELECT hospital_id, hospital_name, db_name, is_active "
            "FROM hospital_tenant WHERE hospital_id = :hid"
        ),
        {"hid": req.hospital_id},
    ).fetchone()
    if existing:
        return TenantCreateResponse(
            created=False,
            hospital_id=existing.hospital_id,
            db_name=existing.db_name,
            hospital_name=existing.hospital_name,
            is_active=int(existing.is_active),
        )

    try:
        template_db.execute(
            text("CALL create_hospital_database(:hid)"),
            {"hid": req.hospital_id},
        )
    except Exception:
        logger.exception(
            "CALL create_hospital_database failed for hospital_id=%s",
            req.hospital_id,
        )
        raise

    db_name = f"hospital_{req.hospital_id}"
    try:
        template_db.execute(
            text(
                "INSERT INTO hospital_tenant "
                "(hospital_id, hospital_name, db_name, is_active) "
                "VALUES (:hid, :hname, :dbname, 1)"
            ),
            {"hid": req.hospital_id, "hname": req.hospital_name, "dbname": db_name},
        )
        template_db.commit()
    except Exception:
        template_db.rollback()
        logger.warning(
            "hospital_tenant INSERT failed for %s; orphan database %r left behind",
            req.hospital_id,
            db_name,
        )
        raise

    return TenantCreateResponse(
        created=True,
        hospital_id=req.hospital_id,
        db_name=db_name,
        hospital_name=req.hospital_name,
        is_active=1,
    )