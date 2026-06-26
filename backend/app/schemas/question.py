from pydantic import BaseModel, Field
from datetime import datetime
from app.models.question import QuestionStatus


class QuestionResponse(BaseModel):
    id: int
    assignment_id: int
    question_number: int
    question_type: str | None
    image_url: str
    student_answer: str | None
    correct_answer: str | None
    score: float | None
    full_score: float | None
    analysis_detail: str | None
    knowledge_points: dict | None
    common_mistakes: list | None
    confidence_score: float | None
    page_index: int | None = None
    bbox_x: float | None = None
    bbox_y: float | None = None
    bbox_w: float | None = None
    bbox_h: float | None = None
    status: QuestionStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class QuestionConfirm(BaseModel):
    score: float | None = None
    analysis_detail: str | None = None


class SimilarQuestion(BaseModel):
    id: int
    question_text: str
    answer: str
    knowledge_point: str
    difficulty: str
    question_type: str = ""
    options: list[dict] = []


class SimilarQuestionsResponse(BaseModel):
    similar_questions: list[SimilarQuestion]
