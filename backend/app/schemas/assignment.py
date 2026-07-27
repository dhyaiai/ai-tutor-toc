from pydantic import BaseModel, Field
from datetime import datetime
from app.models.assignment import AssignmentStatus, LayoutType
from app.schemas.question import QuestionResponse


class AssignmentUpload(BaseModel):
    name: str = Field(..., max_length=255)
    grade: str = Field(..., max_length=32)
    subject: str = Field(..., max_length=64)
    semester: str = Field(..., max_length=32)
    usage_month: str = Field(..., max_length=16)
    layout_type: LayoutType


class AssignmentListResponse(BaseModel):
    id: int
    name: str
    grade: str
    subject: str
    semester: str
    usage_month: str
    layout_type: LayoutType
    status: AssignmentStatus
    total_score: float | None
    question_count: int
    error_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AssignmentDetailResponse(BaseModel):
    id: int
    name: str
    grade: str
    subject: str
    semester: str
    usage_month: str
    layout_type: LayoutType
    file_url: str
    status: AssignmentStatus
    total_score: float | None
    ai_summary: str | None
    questions: list[QuestionResponse]
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
