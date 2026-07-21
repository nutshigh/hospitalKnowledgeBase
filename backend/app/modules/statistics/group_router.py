from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.dependencies import require_role
from app.modules.statistics.group_schemas import (
    GroupBy, GroupFilters, ExportFormat, SortKey,
    parse_csv_query,
)
from app.modules.statistics.group_service import (
    get_overview, get_high_risk, stream_high_risk_csv,
)
from app.utils.exceptions import ValidationException

router = APIRouter()


def _filters(
    hospital_ids: Optional[str] = Query(None),
    batch_ids: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    gender: Optional[str] = Query(None),
    age_groups: Optional[str] = Query(None),
    topn: int = Query(10, ge=1, le=100),
) -> GroupFilters:
    return GroupFilters(
        hospital_ids=parse_csv_query(hospital_ids),
        batch_ids=parse_csv_query(batch_ids),
        date_from=date_from,
        date_to=date_to,
        gender=gender,
        age_groups=parse_csv_query(age_groups),
        topn=topn,
    )


@router.get("/group/overview")
def group_overview(
    group_by: GroupBy = Query(...),
    _admin: None = Depends(require_role("admin")),
    filters: GroupFilters = Depends(_filters),
):
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise ValidationException(detail="date_from must be <= date_to")
    return get_overview(group_by, filters)


@router.get("/group/high-risk")
def group_high_risk(
    _admin: None = Depends(require_role("admin")),
    filters: GroupFilters = Depends(_filters),
    sort: SortKey = Query("red_count"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    format: ExportFormat = Query("json"),
):
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise ValidationException(detail="date_from must be <= date_to")
    if format == "csv":
        return StreamingResponse(
            stream_high_risk_csv(filters, sort),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=high-risk.csv"},
        )
    return get_high_risk(filters, sort, page, page_size)
