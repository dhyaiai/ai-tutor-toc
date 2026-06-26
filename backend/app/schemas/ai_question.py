"""AI 生成题目相关 Pydantic 模型"""

from pydantic import BaseModel
from datetime import datetime


class OptionItem(BaseModel):
    label: str
    text: str


class SaveAIQuestionRequest(BaseModel):
    source_question_id: int | None = None
    question_text: str
    answer: str
    question_type: str | None = None
    knowledge_point: str | None = None
    difficulty: str | None = None
    options: list[OptionItem] | None = None


class AnswerItem(BaseModel):
    id: int
    is_correct: bool | None = None
    score: float | None = None
    full_score: float | None = None
    ai_feedback: str | None = None
    selected_options: list[str] | None = None
    answer_text: str | None = None
    answer_image_url: str | None = None
    answered_at: datetime | None = None


class AIQuestionResponse(BaseModel):
    id: int
    source_question_id: int | None = None
    question_text: str
    answer: str
    question_type: str | None = None
    knowledge_point: str | None = None
    difficulty: str | None = None
    options: list[OptionItem] | None = None
    user_answers: list[AnswerItem] | None = None
    created_at: datetime


class SubmitAnswerResponse(BaseModel):
    is_correct: bool
    score: float
    full_score: float
    feedback: str
    correct_answer: str
    selected_options: list[str] | None = None
    answer_text: str | None = None
    answer_image_url: str | None = None
