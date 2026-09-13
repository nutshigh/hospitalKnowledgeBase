import json
from pydantic import BaseModel, ValidationError
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
    group: Optional[str] = None
    # === STRATEGY:v2026-08-16-original-name-display 展示原始名 ===
    # 前端"指标异常/总检建议异常"展示报告原文名(如"窦性心律不齐"),
    # 需要 source 区分结论型条目、explanation 取原始名(item_name 可能被
    # 归一化名/疾病名覆盖)。纯新增字段, 不影响既有消费方。
    # 回退: 删除以下两行即可。
    source: Optional[str] = None
    explanation: Optional[str] = None
    # === END STRATEGY ===


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
    module_order: List[str] = []
    created_at: datetime
    completed_at: Optional[datetime] = None


def parse_summary_text(summary_text: Optional[str]) -> InterpretationReportSchema:
    if not summary_text:
        return InterpretationReportSchema()
    try:
        data = json.loads(summary_text)
        return InterpretationReportSchema(**data)
    except (json.JSONDecodeError, TypeError, ValidationError):
        return InterpretationReportSchema()


class HighRiskItem(BaseModel):
    user_id: str
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