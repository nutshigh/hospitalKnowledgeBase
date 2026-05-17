from sqlalchemy import Column, BigInteger, String, Text, Integer, DateTime, ForeignKey, JSON, func
from app.models.base import Base


class ReportInterpretation(Base):
    __tablename__ = "report_interpretation"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, ForeignKey("report_info.id"), nullable=False)
    overall_level = Column(String(10), nullable=True)
    red_count = Column(Integer, nullable=False, default=0)
    yellow_count = Column(Integer, nullable=False, default=0)
    green_count = Column(Integer, nullable=False, default=0)
    summary_text = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)


class IndicatorJudgment(Base):
    __tablename__ = "indicator_judgment"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    interpretation_id = Column(BigInteger, ForeignKey("report_interpretation.id"), nullable=False)
    indicator_id = Column(BigInteger, ForeignKey("report_indicator.id"), nullable=False)
    item_name = Column(String(100), nullable=False)
    result_value = Column(String(50), nullable=True)
    deviation = Column(String(10), nullable=True)
    color_level = Column(String(10), nullable=True)
    matched_rule_id = Column(BigInteger, nullable=True)
    explanation = Column(Text, nullable=True)
    suggestion = Column(Text, nullable=True)
    knowledge_refs = Column(JSON, nullable=True)


class TriageRule(Base):
    __tablename__ = "triage_rule"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_name = Column(String(100), nullable=False)
    rule_type = Column(String(20), nullable=False)
    indicator_code = Column(String(50), nullable=True)
    conditions = Column(JSON, nullable=False)
    color_level = Column(String(10), nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
