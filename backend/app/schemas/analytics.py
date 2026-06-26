from pydantic import BaseModel


class SubjectAverage(BaseModel):
    subject: str
    average: float
    count: int


class AnalyticsOverview(BaseModel):
    total_assignments: int
    average_score: float
    total_questions: int
    error_rate: float
    subject_averages: list[SubjectAverage]


class ScoreTrendPoint(BaseModel):
    month: str
    average_score: float
    count: int


class ScoreTrendResponse(BaseModel):
    trends: list[ScoreTrendPoint]


class WeakPoint(BaseModel):
    knowledge_point: str
    error_count: int
    total_count: int
    error_rate: float


class WeaknessResponse(BaseModel):
    weak_points: list[WeakPoint]
