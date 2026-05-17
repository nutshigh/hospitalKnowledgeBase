from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class IndicatorJudgmentSchema(BaseModel):
    indicator_id: int
    item_name: str
    result_value: Optional[str] = None
    deviation: Optional[str] = None
    color_level: Optional[str] = None
    explanation: Optional[str] = None
    suggestion: Optional[str] = None


class InterpretationResponse(BaseModel):
    id: int
    report_id: int
    overall_level: Optional[str] = None
    red_count: int
    yellow_count: int
    green_count: int
    summary_text: Optional[str] = None
    status: str
    indicators: List[IndicatorJudgmentSchema] = []
    created_at: datetime
    completed_at: Optional[datetime] = None


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
    rule_name: str = Field(..., min_length=1, max_length=100)
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
