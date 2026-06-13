from sqlalchemy import Column, BigInteger, String, Text, Integer, DateTime, ForeignKey, JSON, func
from app.models.base import Base


class ChatSession(Base):
    __tablename__ = "chat_session"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    hospital_id = Column(String(32), nullable=False)
    report_id = Column(BigInteger, nullable=True)
    title = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, ForeignKey("chat_session.id"), nullable=False)
    role = Column(String(10), nullable=False)
    content = Column(Text, nullable=False)
    knowledge_refs = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
