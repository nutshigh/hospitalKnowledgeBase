import json
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class IndicatorJudgmentSchema(BaseModel):
    indicator_id: int
    item_name: str
    result_value: Optional[str] = None
    unit: Optional[str] = None
    ref_range_low: Optional[str] = None
    ref_range_high: Optional[str] = None
    deviation: Optional[str] = None
    color_level: Optional[str] = None


class InterpretationReportSchema(BaseModel):
    overall_summary: str = ""
    abnormal_focus: str = ""
    trend_note: str = ""
    suggestions: str = ""
    risk_alert: str = ""


class CitationSchema(BaseModel):
    ref_id: int
    entry_id: Optional[int] = None
    title: str = ""
    source: str = "document"


class InterpretationResponse(BaseModel):
    id: int
    report_id: int
    overall_level: Optional[str] = None
    red_count: int
    yellow_count: int
    green_count: int
    status: str
    summaries: InterpretationReportSchema = InterpretationReportSchema()
    references: List[CitationSchema] = []
    quality_note: Optional[str] = None
    indicators: List[IndicatorJudgmentSchema] = []
    created_at: datetime
    completed_at: Optional[datetime] = None


def parse_summary_text(summary_text: Optional[str]) -> InterpretationReportSchema:
    if not summary_text:
        return InterpretationReportSchema()
    try:
        data = json.loads(summary_text)
        return InterpretationReportSchema(**data)
    except (json.JSONDecodeError, TypeError):
        return InterpretationReportSchema()


class HighRiskItem(BaseModel):
    user_id: int
    report_id: int
    name: Optional[str] = None
    unit_name: Optional[str] = None
    red_count: int
    main_indicators: List[str] = []


class HighRiskResponse(BaseModel):
    items: List[dict]
    total: int


class TriageRuleCreate(BaseModel):
    rule_name: str
    rule_type: str
    indicator_code: Optional[str] = None
    conditions: dict
    color_level: str
    priority: int = 0


class TriageRuleUpdate(BaseModel):
    rule_name: Optional[str] = None
    rule_type: Optional[str] = None
    indicator_code: Optional[str] = None
    conditions: Optional[dict] = None
    color_level: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[int] = None


class TriageRuleResponse(BaseModel):
    id: int
    rule_name: str
    rule_type: str
    indicator_code: Optional[str] = None
    conditions: dict
    color_level: str
    priority: int
    is_active: int
    created_at: datetime
    updated_at: datetime