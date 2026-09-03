from sqlalchemy import Column, String, BigInteger, Text, DateTime, ForeignKey, UniqueConstraint, func
from app.models.base import Base


class BatchImport(Base):
    __tablename__ = "batch_import"

    id = Column(String(36), primary_key=True)            # uuid4 hex
    hospital_id = Column(String(32), nullable=False)
    user_id = Column(String(64), nullable=False)
    filename = Column(String(255), nullable=False)
    archive_path = Column(String(512), nullable=False)
    total = Column(BigInteger, default=0)
    parsed_ok = Column(BigInteger, default=0)
    interp_ok = Column(BigInteger, default=0)
    failed = Column(BigInteger, default=0)
    status = Column(String(24), default="uploading", nullable=False)
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    completed_at = Column(DateTime)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class BatchImportFile(Base):
    __tablename__ = "batch_import_file"

    id = Column(String(36), primary_key=True)
    batch_id = Column(String(36), ForeignKey("batch_import.id"), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_size = Column(BigInteger, default=0)
    crc32 = Column(String(8), nullable=False, index=True)
    status = Column(String(24), default="queued", nullable=False)
    failed_stage = Column(String(24))  # "parsing"|"interpretation"|"oversize" (失败阶段)
    dispatch_hospital = Column(String(24))  # 文件名解析出的目标医院(跨院分发时≠批次 hospital_id)
    report_task_id = Column(BigInteger)
    error_message = Column(Text)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("batch_id", "crc32", name="uq_batch_file"),)