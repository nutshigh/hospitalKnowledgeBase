from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date


class ReportIndicatorSchema(BaseModel):
    item_name: str
    item_name_standard: Optional[str] = None
    item_code: Optional[str] = None
    result_value: Optional[str] = None
    unit: Optional[str] = None
    ref_range_low: Optional[str] = None
    ref_range_high: Optional[str] = None
    category: Optional[str] = None


class ReportInfoSchema(BaseModel):
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    report_date: Optional[date] = None
    check_type: Optional[str] = None
    unit_name: Optional[str] = None
    indicators: List[ReportIndicatorSchema] = []


class TaskStatusResponse(BaseModel):
    task_id: int
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class ReportListResponse(BaseModel):
    items: List[dict]
    total: int
    page: int
    page_size: int


class ReportDetailResponse(BaseModel):
    id: int
    task_id: Optional[int] = None
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    report_date: Optional[date] = None
    check_type: Optional[str] = None
    unit_name: Optional[str] = None
    indicators: List[ReportIndicatorSchema] = []
    created_at: datetime
