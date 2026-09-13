from typing import Any, List, Optional
from pydantic import BaseModel


class AnswerItem(BaseModel):
    question_id: int
    answer: Any = None


class SubmitRequest(BaseModel):
    answers: List[AnswerItem]


class TemplateQuestionItem(BaseModel):
    id: Optional[int] = None  # 接受但不使用:PUT 为全量替换,历史 id 无跨库意义
    question_type: str
    question_text: str
    options: Optional[List[str]] = None
    is_required: bool = True
    sort_order: int = 0
    is_active: bool = True


class TemplateSaveRequest(BaseModel):
    name: str = "通用检后随访"
    description: Optional[str] = None
    questions: List[TemplateQuestionItem]
