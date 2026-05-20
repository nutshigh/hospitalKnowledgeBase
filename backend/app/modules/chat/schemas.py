from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class CreateSessionRequest(BaseModel):
    report_id: Optional[int] = None


class SessionResponse(BaseModel):
    id: int
    user_id: int
    report_id: Optional[int] = None
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    id: int
    session_id: int
    role: str
    content: str
    knowledge_refs: Optional[List[dict]] = None
    created_at: datetime

    class Config:
        from_attributes = True
