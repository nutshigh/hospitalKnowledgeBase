from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

GroupBy = Literal["hospital", "batch", "age_group", "gender", "time_month"]
Gender = Literal["M", "F", "男", "女"]
SortKey = Literal["red_count", "age", "report_date"]
ExportFormat = Literal["json", "csv"]


def parse_csv_query(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    seen: list[str] = []
    for tok in value.split(","):
        t = tok.strip()
        if t and t not in seen:
            seen.append(t)
    return seen or None


class GroupFilters(BaseModel):
    hospital_ids: Optional[list[str]] = None
    batch_ids: Optional[list[str]] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    gender: Optional[Gender] = None
    age_groups: Optional[list[str]] = None
    topn: int = 10


class OverviewRow(BaseModel):
    key: str
    label: str
    total_people: int = 0
    red_count: int = 0
    yellow_count: int = 0
    green_count: int = 0
    abnormal_rate: float = 0.0
    by_gender: Optional[list[dict]] = None
    by_age_group: Optional[list[dict]] = None
    top_abnormal_items: Optional[list[dict]] = None
    error: Optional[str] = None


class OverviewResponse(BaseModel):
    group_by: str
    filters: dict
    rows: list[OverviewRow]
    totals: dict


class HighRiskItem(BaseModel):
    hospital_id: str
    hospital_name: str
    report_id: int
    user_id: int
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    report_date: Optional[date] = None
    batch_id: Optional[str] = None
    batch_name: Optional[str] = None
    overall_level: Optional[str] = None
    red_count: int = 0
    yellow_count: int = 0
    summary_text: Optional[str] = None


class HighRiskResponse(BaseModel):
    items: list[HighRiskItem]
    total: int
    page: int
    page_size: int
    filters: dict
