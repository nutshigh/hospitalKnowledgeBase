from pydantic import BaseModel
from typing import Optional
from datetime import date


class DateRangeQuery(BaseModel):
    hospital_id: str
    start_date: date
    end_date: date
    unit_name: Optional[str] = None


class CrossCompareQuery(DateRangeQuery):
    x_dimension: str = "unit"
    y_metric: str = "abnormal_rate"


class TrendQuery(BaseModel):
    hospital_id: str
    indicator: str
    years: int = 5


class ExportRequest(BaseModel):
    hospital_id: str
    template_id: Optional[int] = None
    export_type: str = "pdf"
    start_date: date
    end_date: date
