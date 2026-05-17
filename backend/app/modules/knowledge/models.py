from sqlalchemy import Column, BigInteger, String, Text, Integer, ForeignKey, DateTime, func
from sqlalchemy.orm import relationship
from app.models.base import Base


class KnowledgeCategory(Base):
    __tablename__ = "knowledge_category"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    parent_id = Column(BigInteger, ForeignKey("knowledge_category.id"), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    children = relationship("KnowledgeCategory", backref="parent", remote_side=[id])


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entry"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    category_id = Column(BigInteger, ForeignKey("knowledge_category.id"), nullable=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    source_type = Column(String(20), nullable=False, default="manual")
    source_file = Column(String(500), nullable=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    parent_entry_id = Column(BigInteger, ForeignKey("knowledge_entry.id"), nullable=True)
    vector_id = Column(String(64), nullable=True)
    status = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
