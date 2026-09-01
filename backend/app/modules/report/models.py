from sqlalchemy import Column, BigInteger, String, Text, Integer, Date, DateTime, ForeignKey, func
from app.models.base import Base


class ReportTask(Base):
    __tablename__ = "report_task"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(16), nullable=False)
    original_file_path = Column(String(500), nullable=False)
    original_filename = Column(String(200), nullable=False)
    file_type = Column(String(10), nullable=False)
    file_size = Column(BigInteger, nullable=False, default=0)
    thumbnail_path = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="queued")
    priority = Column(Integer, nullable=False, default=0)
    retry_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)


class ReportInfo(Base):
    __tablename__ = "report_info"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(BigInteger, ForeignKey("report_task.id"), nullable=True)
    user_id = Column(String(16), nullable=False)
    name = Column(String(50), nullable=True)
    gender = Column(String(5), nullable=True)
    age = Column(Integer, nullable=True)
    report_date = Column(Date, nullable=True)
    check_type = Column(String(20), nullable=True)
    unit_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class ReportIndicator(Base):
    __tablename__ = "report_indicator"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_id = Column(BigInteger, ForeignKey("report_info.id"), nullable=False)
    item_name = Column(String(100), nullable=False)
    item_name_standard = Column(String(100), nullable=True)
    item_code = Column(String(50), nullable=True)
    result_value = Column(String(50), nullable=True)
    unit = Column(String(20), nullable=True)
    ref_range_low = Column(String(50), nullable=True)
    ref_range_high = Column(String(50), nullable=True)
    category = Column(String(50), nullable=True)
    raw_text = Column(Text, nullable=True)
