from sqlalchemy import Column, BigInteger, String, Text, Integer, DateTime, JSON, UniqueConstraint, func
from app.models.base import Base


class FollowupTemplate(Base):
    """平台库 hospital_template:随访问卷模板(单套激活,平台管理员维护)。"""
    __tablename__ = "followup_template"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    updated_by = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class FollowupTemplateQuestion(Base):
    """平台库:模板问题(生成时快照进租户库 followup_question)。"""
    __tablename__ = "followup_template_question"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    template_id = Column(BigInteger, nullable=False)
    question_type = Column(String(10), nullable=False)
    question_text = Column(String(500), nullable=False)
    options = Column(JSON, nullable=True)
    is_required = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class Followup(Base):
    """租户库:每份红/黄报告一份随访实例。"""
    __tablename__ = "followup"
    __table_args__ = (UniqueConstraint("report_id", name="uq_followup_report"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, nullable=False)
    user_id = Column(String(16), nullable=False)
    name = Column(String(50), nullable=True)
    overall_level = Column(String(10), nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    recheck_indicators_json = Column(JSON, nullable=True)
    template_name = Column(String(100), nullable=True)
    generated_at = Column(DateTime, default=func.now(), nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class FollowupQuestion(Base):
    """租户库:题目快照 + 用户答案(单表)。"""
    __tablename__ = "followup_question"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    followup_id = Column(BigInteger, nullable=False)
    question_type = Column(String(10), nullable=False)
    question_text = Column(String(500), nullable=False)
    options = Column(JSON, nullable=True)
    is_required = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    answer = Column(Text, nullable=True)
    answered_at = Column(DateTime, nullable=True)


class UserNotification(Base):
    """租户库:站内通知(当前仅 recheck_reminder,预留 category 扩展)。"""
    __tablename__ = "user_notification"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(16), nullable=False)
    name = Column(String(50), nullable=True)
    category = Column(String(24), nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(JSON, nullable=False)
    ref_report_id = Column(BigInteger, nullable=True)
    ref_followup_id = Column(BigInteger, nullable=True)
    is_read = Column(Integer, nullable=False, default=0)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
