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
    group: Optional[str] = None


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


class ImagingItemSchema(BaseModel):
    name: str
    level: str


class ReportDetailResponse(BaseModel):
    id: int
    task_id: Optional[int] = None
    name: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    report_date: Optional[date] = None
    check_type: Optional[str] = None
    unit_name: Optional[str] = None
    conclusion_text: Optional[str] = None
    indicators: List[ReportIndicatorSchema] = []
    module_order: List[str] = []
    # 2026-10-14: 影像检查 11 项红黄绿总览(纯追加字段; 见 imaging_items.py)
    imaging_items: List[ImagingItemSchema] = []
    created_at: datetime
