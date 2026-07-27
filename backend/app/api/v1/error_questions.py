from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.assignment import Assignment
from app.models.question import Question
from app.services.file_upload import StorageService
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/error-questions", tags=["error-questions"])

# 合法的题型白名单（与前端 QUESTION_TYPE_OPTIONS 保持一致）
_VALID_QUESTION_TYPES = frozenset({
    "单选题", "多选题", "填空题", "计算题", "应用题", "证明题",
    "简答题", "判断题", "阅读理解", "完形填空", "写作题", "作图题",
})


def _escape_like(value: str) -> str:
    """转义 LIKE 模式中的通配符 % 和 _，防止用户输入被当作通配符匹配。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _build_child_item(child: Question, storage: StorageService) -> dict:
    """将子题 ORM 对象转为前端需要的 dict 格式"""
    score_rate = (
        round(float(child.score) / float(child.full_score), 4)
        if child.score is not None and child.full_score
        else 0.0
    )
    return {
        "id": child.id,
        "sub_question_index": child.sub_question_index,
        "question_type": child.question_type,
        "student_answer": child.student_answer,
        "correct_answer": child.correct_answer,
        "score": child.score,
        "full_score": child.full_score,
        "score_rate": score_rate,
        "knowledge_points": child.knowledge_points,
        "common_mistakes": child.common_mistakes,
        "analysis_detail": child.analysis_detail,
    }


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
    """
    获取当前用户的错题列表。

    大题聚合逻辑：
    - 独立题（无父无子）：直接返回
    - 子题（parent_id IS NOT NULL）：按父题聚合，只展示父题卡片
    - 父题容器本身 score=NULL，不会被 score<full_score 查到，需通过子题反查
    """
    # 构建叶子题（子题 + 独立题）的错题查询
    leaf_query = (
        select(Question, Assignment.name.label("assignment_name"), Assignment.id.label("assignment_id"))
        .join(Assignment, Question.assignment_id == Assignment.id)
        .where(
            Assignment.creator_id == current_user.id,
            Question.score < Question.full_score,
        )
    )

    # 应用筛选条件
    if grade:
        leaf_query = leaf_query.where(Assignment.grade == grade)
    if subject:
        leaf_query = leaf_query.where(Assignment.subject == subject)
    if semester:
        leaf_query = leaf_query.where(Assignment.semester == semester)
    if question_type:
        if question_type not in _VALID_QUESTION_TYPES:
            raise HTTPException(status_code=400, detail=f"无效的题型: {question_type}")
        leaf_query = leaf_query.where(Question.question_type == question_type)
    if search:
        leaf_query = leaf_query.where(Assignment.name.ilike(f"%{_escape_like(search)}%", escape="\\"))
    if score_rate_min is not None:
        leaf_query = leaf_query.where((Question.score / Question.full_score) >= score_rate_min)
    if score_rate_max is not None:
        leaf_query = leaf_query.where((Question.score / Question.full_score) <= score_rate_max)

    leaf_query = leaf_query.order_by(desc(Question.created_at))
    result = await db.execute(leaf_query)
    rows = result.all()

    # ── 分类：独立题 vs 子题（按父题聚合）──
    # 找出所有是"父题容器"的题目ID（被其他题目通过 parent_id 引用的）
    # 先收集所有可能的 parent_id
    parent_ids_with_errors: set[int] = set()
    standalone_rows: list[tuple[Question, str, int]] = []  # (question, assignment_name, assignment_id)

    for question, assignment_name, assignment_id in rows:
        if question.parent_id is not None:
            # 子题 → 记录其父题ID
            parent_ids_with_errors.add(question.parent_id)
        else:
            # parent_id IS NULL → 可能是独立题或父题容器
            # 父题容器 score=NULL，不会被查出来，所以这里全是独立题
            standalone_rows.append((question, assignment_name, assignment_id))

    storage = StorageService()

    # ── 构建独立题的返回项 ──
    items: list[dict] = []
    for question, assignment_name, assignment_id in standalone_rows:
        score_rate = (
            round(float(question.score) / float(question.full_score), 4)
            if question.score is not None and question.full_score
            else 0.0
        )
        items.append({
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
            "is_big_question": False,
        })

    # ── 构建父题聚合项 ──
    if parent_ids_with_errors:
        # 加载父题信息
        parent_query = select(Question).where(Question.id.in_(parent_ids_with_errors))
        parent_result = await db.execute(parent_query)
        parents_by_id: dict[int, Question] = {p.id: p for p in parent_result.scalars().all()}

        # 加载这些父题的所有子题（不仅是错题，是整个大题的所有小题）
        all_children_query = (
            select(Question)
            .where(Question.parent_id.in_(parent_ids_with_errors))
            .order_by(Question.parent_id, Question.sub_question_index)
        )
        children_result = await db.execute(all_children_query)
        children_by_parent: dict[int, list[Question]] = {}
        for child in children_result.scalars().all():
            children_by_parent.setdefault(child.parent_id, []).append(child)

        # 加载父题对应的作业名称
        parent_assignment_ids = {p.assignment_id for p in parents_by_id.values()}
        assignment_query = select(Assignment.id, Assignment.name).where(
            Assignment.id.in_(parent_assignment_ids)
        )
        assignment_result = await db.execute(assignment_query)
        assignment_names: dict[int, str] = {row[0]: row[1] for row in assignment_result.all()}

        for parent_id in parent_ids_with_errors:
            parent = parents_by_id.get(parent_id)
            if not parent:
                continue
            children = children_by_parent.get(parent_id, [])
            if not children:
                continue

            # 统计错题数
            error_count = sum(
                1 for c in children
                if c.score is not None and c.full_score is not None and c.score < c.full_score
            )
            total_child_score = sum(c.score for c in children if c.score is not None)
            total_child_full = sum(c.full_score for c in children if c.full_score is not None)
            score_rate = (
                round(total_child_score / total_child_full, 4)
                if total_child_full > 0 else 0.0
            )

            # 构建子题列表
            children_items = []
            for child in children:
                children_items.append(await _build_child_item(child, storage))

            items.append({
                "id": parent.id,
                "assignment_id": parent.assignment_id,
                "assignment_name": assignment_names.get(parent.assignment_id, ""),
                "question_number": parent.question_number,
                "question_type": parent.question_type,
                "image_url": await storage.get_presigned_url(parent.image_url),
                "score_rate": score_rate,
                "knowledge_points": parent.knowledge_points,
                "common_mistakes": parent.common_mistakes,
                "analysis_detail": parent.analysis_detail,
                "created_at": parent.created_at,
                "is_big_question": True,
                "children": children_items,
                "error_count": error_count,
                "total_count": len(children),
            })

    # ── 按创建时间倒序排列 ──
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    # ── 分页 ──
    total = len(items)
    page_items = items[(page - 1) * page_size : page * page_size]

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
