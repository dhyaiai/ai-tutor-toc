from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.assignment import Assignment
from app.models.question import Question
from app.services.file_upload import StorageService

router = APIRouter(prefix="/error-questions", tags=["error-questions"])

# 合法的题型白名单（与前端 QUESTION_TYPE_OPTIONS 保持一致）
_VALID_QUESTION_TYPES = frozenset({
    "选择题", "填空题", "计算题", "应用题", "证明题",
    "简答题", "判断题", "阅读理解", "完形填空", "写作题", "作图题",
})


def _escape_like(value: str) -> str:
    """转义 LIKE 模式中的通配符 % 和 _，防止用户输入被当作通配符匹配。"""
    # 先转义转义符本身，再转义 % 和 _
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("")
async def list_error_questions(
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    semester: str | None = Query(None),
    question_type: str | None = Query(None),
    score_rate_min: float | None = Query(None, ge=0, le=1),
    score_rate_max: float | None = Query(None, ge=0, le=1),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Base: user's error questions
    query = (
        select(Question, Assignment.name.label("assignment_name"))
        .join(Assignment, Question.assignment_id == Assignment.id)
        .where(
            Assignment.creator_id == current_user.id,
            Question.score < Question.full_score,
        )
    )

    if grade:
        query = query.where(Assignment.grade == grade)
    if subject:
        query = query.where(Assignment.subject == subject)
    if semester:
        query = query.where(Assignment.semester == semester)
    if question_type:
        if question_type not in _VALID_QUESTION_TYPES:
            raise HTTPException(status_code=400, detail=f"无效的题型: {question_type}")
        query = query.where(Question.question_type == question_type)
    if search:
        query = query.where(Assignment.name.ilike(f"%{_escape_like(search)}%", escape="\\"))
    if score_rate_min is not None:
        query = query.where((Question.score / Question.full_score) >= score_rate_min)
    if score_rate_max is not None:
        query = query.where((Question.score / Question.full_score) <= score_rate_max)

    # Build count query with same conditions
    count_query = (
        select(func.count())
        .select_from(Question)
        .join(Assignment, Question.assignment_id == Assignment.id)
        .where(
            Assignment.creator_id == current_user.id,
            Question.score < Question.full_score,
        )
    )
    if grade:
        count_query = count_query.where(Assignment.grade == grade)
    if subject:
        count_query = count_query.where(Assignment.subject == subject)
    if semester:
        count_query = count_query.where(Assignment.semester == semester)
    if question_type:
        count_query = count_query.where(Question.question_type == question_type)
    if search:
        count_query = count_query.where(Assignment.name.ilike(f"%{_escape_like(search)}%", escape="\\"))
    if score_rate_min is not None:
        count_query = count_query.where((Question.score / Question.full_score) >= score_rate_min)
    if score_rate_max is not None:
        count_query = count_query.where((Question.score / Question.full_score) <= score_rate_max)

    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.order_by(desc(Question.created_at)).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    rows = result.all()

    storage = StorageService()
    items = []
    for question, assignment_name in rows:
        score_rate = (
            round(float(question.score) / float(question.full_score), 4)
            if question.score is not None and question.full_score
            else 0.0
        )

        items.append(
            {
                "id": question.id,
                "assignment_id": question.assignment_id,
                "assignment_name": assignment_name,
                "question_number": question.question_number,
                "question_type": question.question_type,
                "image_url": await storage.get_presigned_url(question.image_url),
                "student_answer": question.student_answer,
                "correct_answer": question.correct_answer,
                "score": question.score,
                "full_score": question.full_score,
                "score_rate": score_rate,
                "knowledge_points": question.knowledge_points,
                "common_mistakes": question.common_mistakes,
                "analysis_detail": question.analysis_detail,
                "created_at": question.created_at,
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
