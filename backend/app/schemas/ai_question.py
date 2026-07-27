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
    analysis: str | None = None
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
    """单个AI题目详情（供 GET /{id} 使用）"""
    id: int
    source_question_id: int | None = None
    question_text: str
    answer: str
    analysis: str | None = None
    question_type: str | None = None
    knowledge_point: str | None = None
    difficulty: str | None = None
    options: list[OptionItem] | None = None
    user_answers: list[AnswerItem] | None = None
    created_at: datetime


class AISubQuestionResponse(BaseModel):
    """大题中的子题"""
    id: int
    sub_question_index: int
    question_text: str
    answer: str
    analysis: str | None = None
    question_type: str | None = None
    knowledge_point: str | None = None
    difficulty: str | None = None
    options: list[OptionItem] | None = None
    user_answers: list[AnswerItem] | None = None
    created_at: datetime | None = None


class AIQuestionListItem(BaseModel):
    """列表中的单个条目——可能是独立题或大题"""
    # --- 独立题字段 ---
    id: int | None = None  # 大题没有独立 id，设为 None
    source_question_id: int | None = None
    question_text: str = ""
    answer: str = ""
    analysis: str | None = None
    question_type: str | None = None
    knowledge_point: str | None = None
    difficulty: str | None = None
    options: list[OptionItem] | None = None
    user_answers: list[AnswerItem] | None = None
    created_at: datetime | None = None

    # --- 大题字段（可选） ---
    is_big_question: bool = False
    group_id: str | None = None
    question_context: str | None = None
    children: list[AISubQuestionResponse] | None = None
    total_count: int | None = None
    score_rate: float | None = None

    class Config:
        from_attributes = True


class SubmitAnswerResponse(BaseModel):
    question_id: int | None = None
    is_correct: bool
    score: float
    full_score: float
    feedback: str
    correct_answer: str
    selected_options: list[str] | None = None
    answer_text: str | None = None
    answer_image_url: str | None = None
