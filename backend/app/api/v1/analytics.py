from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.assignment import Assignment, AssignmentStatus
from app.models.question import Question

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview")
async def get_overview(
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Build base filter
    base_filter = [
        Assignment.creator_id == current_user.id,
        Assignment.status == AssignmentStatus.COMPLETED,
    ]
    if grade:
        base_filter.append(Assignment.grade == grade)
    if subject:
        base_filter.append(Assignment.subject == subject)

    # Total assignments
    count_stmt = select(func.count()).select_from(Assignment).where(*base_filter)
    total_assignments = (await db.execute(count_stmt)).scalar() or 0

    # Total questions & average score
    q_stmt = (
        select(
            func.count(Question.id),
            func.avg(Question.score),
            func.sum(case((Question.score < Question.full_score, 1), else_=0)),
        )
        .select_from(Question)
        .join(Assignment, Question.assignment_id == Assignment.id)
        .where(*base_filter)
    )
    q_result = await db.execute(q_stmt)
    total_questions, avg_score, error_count = q_result.one()
    total_questions = total_questions or 0
    avg_score = float(avg_score) if avg_score else 0.0
    error_count = error_count or 0
    error_rate = error_count / total_questions if total_questions > 0 else 0.0

    # Per-subject averages
    subj_stmt = (
        select(
            Assignment.subject,
            func.avg(Assignment.total_score).label("avg_score"),
            func.count(Assignment.id).label("cnt"),
        )
        .where(*base_filter)
        .group_by(Assignment.subject)
    )
    subj_result = await db.execute(subj_stmt)
    subject_averages = [
        {"subject": row[0], "average": float(row[1]) if row[1] else 0.0, "count": row[2]}
        for row in subj_result.all()
    ]

    return {
        "total_assignments": total_assignments,
        "average_score": round(avg_score, 2),
        "total_questions": total_questions,
        "error_rate": round(error_rate, 4),
        "subject_averages": subject_averages,
    }


@router.get("/score-trend")
async def get_score_trend(
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    semester: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    filter_conds = [
        Assignment.creator_id == current_user.id,
        Assignment.status == AssignmentStatus.COMPLETED,
    ]
    if grade:
        filter_conds.append(Assignment.grade == grade)
    if subject:
        filter_conds.append(Assignment.subject == subject)
    if semester:
        filter_conds.append(Assignment.semester == semester)

    stmt = (
        select(
            Assignment.month,
            func.avg(Assignment.total_score).label("avg_score"),
            func.count(Assignment.id).label("cnt"),
        )
        .where(*filter_conds)
        .group_by(Assignment.month)
        .order_by(Assignment.month)
    )
    result = await db.execute(stmt)
    trends = [
        {"month": row[0], "average_score": float(row[1]) if row[1] else 0.0, "count": row[2]}
        for row in result.all()
    ]
    return {"trends": trends}


@router.get("/weakness")
async def get_weakness(
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    semester: str | None = Query(None),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Get error questions with knowledge_points
    filter_conds = [
        Assignment.creator_id == current_user.id,
        Question.score < Question.full_score,
        Question.knowledge_points.isnot(None),
    ]
    if grade:
        filter_conds.append(Assignment.grade == grade)
    if subject:
        filter_conds.append(Assignment.subject == subject)
    if semester:
        filter_conds.append(Assignment.semester == semester)

    stmt = (
        select(Question.knowledge_points, Question.score, Question.full_score)
        .join(Assignment, Question.assignment_id == Assignment.id)
        .where(*filter_conds)
    )
    result = await db.execute(stmt)
    rows = result.all()

    # Aggregate knowledge point errors
    kp_errors: dict[str, int] = {}
    kp_total: dict[str, int] = {}
    for knowledge_points, score, full_score in rows:
        if not knowledge_points:
            continue
        for kp in knowledge_points:
            kp_name = kp if isinstance(kp, str) else kp.get("name", str(kp))
            kp_errors[kp_name] = kp_errors.get(kp_name, 0) + 1
            kp_total[kp_name] = kp_total.get(kp_name, 0) + 1

    # Also count total occurrences from all questions
    total_stmt = (
        select(Question.knowledge_points)
        .join(Assignment, Question.assignment_id == Assignment.id)
        .where(
            Assignment.creator_id == current_user.id,
            Question.knowledge_points.isnot(None),
        )
    )
    if grade:
        total_stmt = total_stmt.where(Assignment.grade == grade)
    if subject:
        total_stmt = total_stmt.where(Assignment.subject == subject)
    if semester:
        total_stmt = total_stmt.where(Assignment.semester == semester)
    total_result = await db.execute(total_stmt)
    for (kps,) in total_result.all():
        if not kps:
            continue
        for kp in kps:
            kp_name = kp if isinstance(kp, str) else kp.get("name", str(kp))
            kp_total[kp_name] = kp_total.get(kp_name, 0) + 1

    weak_points = sorted(
        [
            {
                "knowledge_point": kp,
                "error_count": kp_errors.get(kp, 0),
                "total_count": kp_total.get(kp, 0),
                "error_rate": round(kp_errors.get(kp, 0) / kp_total.get(kp, 1), 4),
            }
            for kp in kp_errors
        ],
        key=lambda x: x["error_rate"],
        reverse=True,
    )[:limit]

    return {"weak_points": weak_points}
