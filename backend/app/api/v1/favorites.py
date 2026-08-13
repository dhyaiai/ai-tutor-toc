"""我的收藏接口：收藏 / 取消收藏 / 收藏列表（错题 + AI 题混排）

设计要点：
- 收藏实体为"错题"（assignment_questions）或"AI 生成题"（ai_generated_questions），
  user_favorites 表用 (user_id, item_type, question_id) 唯一约束，question_id 无跨表外键。
- AI 大题无独立 id（只有 group_id），收藏时以"组内 sub_question_index 最小行"为锚点存储；
  展示时按锚点反查 group_id 聚合为完整大题（与 /ai-questions 列表语义一致）。
- 锚点归一化函数 _normalize_anchor 必须被 POST 与 DELETE 共用，
  保证删除键与存储锚点一致。
- 收藏列表按收藏时间倒序（tiebreaker：favorite_id），Python 侧分页（个人收藏量级小，
  且 /ai-questions 已有 Python 侧分页先例）。
"""

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.favorite import UserFavorite
from app.models.question import Question
from app.models.assignment import Assignment
from app.models.ai_question import AIGeneratedQuestion, AIQuestionAnswer
from app.services.file_upload import StorageService
# 复用错题列表的题型白名单与子题序列化函数，保证收藏页与错题列表展示完全一致
from app.api.v1.error_questions import _VALID_QUESTION_TYPES, _build_child_item_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/favorites", tags=["favorites"])

# 合法收藏类型（与前端 FavoriteItemType 保持一致）
_VALID_ITEM_TYPES = frozenset({"error", "ai"})

# 合法题目来源（与前端"题目来源"筛选保持一致）：
# error=错题；ai=AI 生成题；upload=自有试题（上传转录，item_type 仍为 "ai"，靠 source 列区分）
_VALID_SOURCES = frozenset({"error", "ai", "upload"})


async def _filter_ai_favs_by_source(
    db: AsyncSession, favs: list[UserFavorite], source: str
) -> list[UserFavorite]:
    """按来源列过滤 AI 类收藏：source='upload' 保留自有试题，source='ai' 排除自有试题。

    一次批量查询 source 映射（防 N+1），锚点题已被删除的收藏在映射缺失时视为
    AI 生成（老数据），直接丢弃（后续构建条目时也会因查无题而跳过）。
    """
    anchor_ids = {f.question_id for f in favs}
    source_map: dict[int, str] = {}
    if anchor_ids:
        rows = await db.execute(
            select(AIGeneratedQuestion.id, AIGeneratedQuestion.source).where(
                AIGeneratedQuestion.id.in_(anchor_ids)
            )
        )
        source_map = {r[0]: r[1] for r in rows.all()}
    want_upload = source == "upload"
    return [
        f for f in favs
        if (source_map.get(f.question_id) == "upload") == want_upload
    ]


class AddFavoriteRequest(BaseModel):
    """添加收藏请求体"""
    item_type: str  # error=错题, ai=AI生成题
    question_id: int


async def _normalize_anchor(
    db: AsyncSession, current_user: User, item_type: str, question_id: int
) -> int | None:
    """锚点归一化：把前端传入的题目 id 转为实际存储的锚点 id。

    - error：若传入子题 id，归一化为父题 id（错题列表以父题卡片为收藏单元）
    - ai：若传入大题组内成员，归一化为组内 sub_question_index 最小行的 id（同组最多一条收藏）
    POST 与 DELETE 必须共用此函数，保证删除键与存储锚点一致。
    题目不存在或不属于当前用户时返回 None。
    """
    if item_type == "error":
        q = await db.get(Question, question_id)
        if q is None:
            return None
        # 归属校验：错题属于某作业，作业创建者必须是当前用户
        owner_row = await db.execute(
            select(Assignment.id).where(
                Assignment.id == q.assignment_id,
                Assignment.creator_id == current_user.id,
            )
        )
        if owner_row.scalar_one_or_none() is None:
            return None
        # 子题 → 父题；独立题 → 自身
        return q.parent_id if q.parent_id is not None else q.id
    else:
        q = await db.get(AIGeneratedQuestion, question_id)
        if q is None or q.user_id != current_user.id:
            return None
        if not q.group_id:
            return q.id
        # 大题组内：找 sub_question_index 最小（NULL 视为 0）的行作为锚点
        rows = (
            await db.execute(
                select(AIGeneratedQuestion)
                .where(AIGeneratedQuestion.group_id == q.group_id)
                .order_by(
                    func.coalesce(AIGeneratedQuestion.sub_question_index, 0).asc(),
                    AIGeneratedQuestion.id.asc(),
                )
            )
        ).scalars().all()
        if not rows:
            return None
        return rows[0].id


@router.post("")
async def add_favorite(
    body: AddFavoriteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """添加收藏（幂等：重复收藏同一题视为成功，不重复插入）"""
    if body.item_type not in _VALID_ITEM_TYPES:
        raise HTTPException(status_code=400, detail=f"无效的收藏类型: {body.item_type}")

    anchor_id = await _normalize_anchor(db, current_user, body.item_type, body.question_id)
    if anchor_id is None:
        raise HTTPException(status_code=404, detail="题目不存在或无权收藏")

    # 幂等插入：先查后插（避免依赖唯一约束冲突 + rollback 的 AsyncSession 陷阱）。
    # 注意：
    # 1. rollback() 会使 session 内 ORM 对象属性全部过期，之后访问会触发惰性加载
    #    （AsyncSession 不支持隐式异步 IO，抛 MissingGreenlet），故先取出普通变量；
    # 2. 查询显式关闭 autoflush：flush 冲突后残留的 pending 对象若被 autoflush 重试，
    #    会再次 INSERT（Duplicate）并在属性过期时触发惰性加载。
    user_id = current_user.id
    fav_filter = (
        select(UserFavorite).where(
            UserFavorite.user_id == user_id,
            UserFavorite.item_type == body.item_type,
            UserFavorite.question_id == anchor_id,
        )
    )
    existing = (
        await db.execute(fav_filter, execution_options={"autoflush": False})
    ).scalar_one_or_none()
    if existing is not None:
        return {"id": existing.id, "item_type": body.item_type, "question_id": anchor_id}

    fav = UserFavorite(user_id=user_id, item_type=body.item_type, question_id=anchor_id)
    db.add(fav)
    try:
        await db.flush()
    except IntegrityError:
        # 并发兜底：极端情况下另一请求已插入（唯一约束冲突），视为幂等成功。
        # rollback 后不再访问任何 ORM 对象属性，直接返回已有记录。
        await db.rollback()
        fav_row = (
            await db.execute(fav_filter, execution_options={"autoflush": False})
        ).scalar_one_or_none()
        if fav_row is not None:
            return {"id": fav_row.id, "item_type": body.item_type, "question_id": anchor_id}
        raise

    return {"id": fav.id, "item_type": body.item_type, "question_id": anchor_id}


@router.delete("")
async def remove_favorite(
    item_type: str = Query(...),
    question_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消收藏（幂等：未收藏或题目不存在时也返回成功）"""
    if item_type not in _VALID_ITEM_TYPES:
        raise HTTPException(status_code=400, detail=f"无效的收藏类型: {item_type}")

    # 先归一化再删除，保证与 POST 存储的锚点一致
    anchor_id = await _normalize_anchor(db, current_user, item_type, question_id)
    if anchor_id is None:
        return {"deleted": 0}

    result = await db.execute(
        select(UserFavorite).where(
            UserFavorite.user_id == current_user.id,
            UserFavorite.item_type == item_type,
            UserFavorite.question_id == anchor_id,
        )
    )
    fav = result.scalar_one_or_none()
    if fav is None:
        return {"deleted": 0}
    await db.delete(fav)
    return {"deleted": 1}


@router.get("")
async def list_favorites(
    item_type: str | None = Query(None),
    source: str | None = Query(None, description="题目来源：error=错题, ai=AI题, upload=自有试题"),
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    semester: str | None = Query(None),
    question_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """收藏列表：错题与 AI 题混排，按收藏时间倒序，支持年级/学期/科目/题型/来源筛选。

    返回 envelope 结构 {item_type, source, favorite_id, favorited_at, question}，
    question 的字段与 /error-questions、/ai-questions 列表项完全一致，
    前端按 item_type 分流直接复用对应题目卡片组件。
    """
    if item_type and item_type not in _VALID_ITEM_TYPES:
        raise HTTPException(status_code=400, detail=f"无效的收藏类型: {item_type}")
    if source and source not in _VALID_SOURCES:
        raise HTTPException(status_code=400, detail=f"无效的题目来源: {source}")

    # 取当前用户全部收藏（按收藏时间倒序，id 兜底保证同秒收藏顺序稳定）
    fav_q = select(UserFavorite).where(UserFavorite.user_id == current_user.id)
    if item_type:
        fav_q = fav_q.where(UserFavorite.item_type == item_type)
    favs = (
        await db.execute(fav_q.order_by(desc(UserFavorite.created_at), desc(UserFavorite.id)))
    ).scalars().all()

    error_favs = [f for f in favs if f.item_type == "error"]
    ai_favs = [f for f in favs if f.item_type == "ai"]

    # 题目来源筛选：source=error 只看错题；source=ai 只看 AI 生成（排除自有试题）；
    # source=upload 只看自有试题（上传转录）。自有试题与 AI 生成共用 item_type="ai"，
    # 需查 ai_generated_questions.source 列区分
    if source == "error":
        ai_favs = []
    elif source in ("ai", "upload"):
        error_favs = []
        ai_favs = await _filter_ai_favs_by_source(db, ai_favs, source)

    entries: list[dict] = []
    if error_favs:
        entries.extend(
            await _build_error_entries(db, error_favs, grade, subject, semester, question_type)
        )
    if ai_favs:
        entries.extend(
            await _build_ai_entries(db, current_user, ai_favs, grade, subject, semester, question_type)
        )

    # 按收藏时间倒序（tiebreaker：favorite_id 倒序，同秒收藏时翻页顺序稳定）
    entries.sort(key=lambda e: (e["favorited_at"], e["favorite_id"]), reverse=True)
    total = len(entries)
    page_entries = entries[(page - 1) * page_size : page * page_size]

    return {"items": page_entries, "total": total, "page": page, "page_size": page_size}


# ═══════════════════════════════════════════════════════════════════
# 以下为两组（错题 / AI 题）的查询与构建逻辑，字段与两个列表接口保持完全一致
# ═══════════════════════════════════════════════════════════════════

async def _build_error_entries(
    db: AsyncSession,
    favs: list[UserFavorite],
    grade: str | None,
    subject: str | None,
    semester: str | None,
    question_type: str | None,
) -> list[dict]:
    """构建错题收藏项，结构对齐 /error-questions 列表接口（含大题聚合）。"""
    fav_by_id = {f.question_id: f for f in favs}
    anchor_ids = list(fav_by_id.keys())

    conditions = [Question.id.in_(anchor_ids)]
    if grade:
        conditions.append(Assignment.grade == grade)
    if subject:
        conditions.append(Assignment.subject == subject)
    if semester:
        conditions.append(Assignment.semester == semester)
    if question_type:
        if question_type not in _VALID_QUESTION_TYPES:
            raise HTTPException(status_code=400, detail=f"无效的题型: {question_type}")
        conditions.append(Question.question_type == question_type)

    # 注意：与错题列表不同，这里【不加】score < full_score 条件——
    # 收藏的题目即使之后重判分，也应保留在收藏夹中
    questions = (
        await db.execute(
            select(Question)
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(*conditions)
        )
    ).scalars().all()

    # 孤儿收藏（锚点题已被删除）静默跳过
    if not questions:
        return []

    # 父题容器（score IS NULL）加载全部子题，卡片需展示完整大题
    parent_ids = [q.id for q in questions if q.score is None]
    children_by_parent: dict[int, list[Question]] = {}
    if parent_ids:
        children_result = await db.execute(
            select(Question)
            .where(Question.parent_id.in_(parent_ids))
            .order_by(Question.parent_id, Question.sub_question_index)
        )
        for child in children_result.scalars().all():
            children_by_parent.setdefault(child.parent_id, []).append(child)

    # 批量加载作业元数据（名称 + 年级 + 科目：收藏页展示题目的年级/科目）
    assignment_ids = {q.assignment_id for q in questions}
    assignment_meta: dict[int, dict] = {}
    if assignment_ids:
        meta_result = await db.execute(
            select(Assignment.id, Assignment.name, Assignment.grade, Assignment.subject)
            .where(Assignment.id.in_(assignment_ids))
        )
        assignment_meta = {
            row[0]: {"name": row[1], "grade": row[2], "subject": row[3]}
            for row in meta_result.all()
        }

    # 预签名图片 URL（asyncio.gather 并发，避免 N+1 IO）
    storage = StorageService()
    presigned_urls: dict[str, str] = {}
    all_urls = [q.image_url for q in questions if q.image_url]
    if all_urls:
        presigned_results = await asyncio.gather(
            *[storage.get_presigned_url(url) for url in all_urls],
            return_exceptions=True,
        )
        for url, presigned in zip(all_urls, presigned_results):
            if not isinstance(presigned, Exception):
                presigned_urls[url] = presigned

    entries: list[dict] = []
    for q in questions:
        fav = fav_by_id[q.id]
        # 作业元数据（名称 + 年级/科目），用于卡片展示
        meta = assignment_meta.get(q.assignment_id, {})

        if q.score is None:
            # ── 父题聚合项（score 为 NULL 的容器）──
            children = children_by_parent.get(q.id, [])
            if not children:
                continue  # 无子题的父题容器（数据异常）跳过
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
            entries.append({
                "item_type": "error",
                "source": "error",
                "favorite_id": fav.id,
                "favorited_at": fav.created_at,
                "question": {
                    "id": q.id,
                    "assignment_id": q.assignment_id,
                    "assignment_name": meta.get("name", ""),
                    "grade": meta.get("grade"),
                    "subject": meta.get("subject"),
                    "question_number": q.question_number,
                    "question_type": q.question_type,
                    "image_url": presigned_urls.get(q.image_url, q.image_url),
                    "question_text": q.question_text,
                    "score_rate": score_rate,
                    "knowledge_points": q.knowledge_points,
                    "common_mistakes": q.common_mistakes,
                    "analysis_detail": q.analysis_detail,
                    "created_at": q.created_at,
                    "is_big_question": True,
                    # 传入父题完整题干：历史数据子题复制了整套大题干时聚合层去重
                    "children": [_build_child_item_sync(c, q.question_text) for c in children],
                    "error_count": error_count,
                    "total_count": len(children),
                    "is_favorited": True,
                },
            })
        else:
            # ── 独立题 ──
            score_rate = (
                round(float(q.score) / float(q.full_score), 4)
                if q.score is not None and q.full_score
                else 0.0
            )
            entries.append({
                "item_type": "error",
                "source": "error",
                "favorite_id": fav.id,
                "favorited_at": fav.created_at,
                "question": {
                    "id": q.id,
                    "assignment_id": q.assignment_id,
                    "assignment_name": meta.get("name", ""),
                    "grade": meta.get("grade"),
                    "subject": meta.get("subject"),
                    "question_number": q.question_number,
                    "question_type": q.question_type,
                    "image_url": presigned_urls.get(q.image_url, q.image_url),
                    "question_text": q.question_text,
                    "student_answer": q.student_answer,
                    "correct_answer": q.correct_answer,
                    "score": q.score,
                    "full_score": q.full_score,
                    "score_rate": score_rate,
                    "knowledge_points": q.knowledge_points,
                    "common_mistakes": q.common_mistakes,
                    "analysis_detail": q.analysis_detail,
                    "created_at": q.created_at,
                    "is_big_question": False,
                    "is_favorited": True,
                },
            })
    return entries


async def _build_ai_entries(
    db: AsyncSession,
    current_user: User,
    favs: list[UserFavorite],
    grade: str | None,
    subject: str | None,
    semester: str | None,
    question_type: str | None,
) -> list[dict]:
    """构建 AI 题收藏项，结构对齐 /ai-questions 列表接口（含大题分组聚合）。"""
    fav_by_id = {f.question_id: f for f in favs}
    anchor_ids = list(fav_by_id.keys())

    # 载入锚点题（独立题或大题成员）
    anchors = (
        await db.execute(
            select(AIGeneratedQuestion).where(AIGeneratedQuestion.id.in_(anchor_ids))
        )
    ).scalars().all()
    if not anchors:
        return []
    anchor_map = {q.id: q for q in anchors}

    # 锚点所在组 → 收藏记录映射（组内展示的 fav 时间/id 以锚点记录为准）
    group_fav: dict[str, UserFavorite] = {}
    for f in favs:
        anchor_q = anchor_map.get(f.question_id)
        if anchor_q and anchor_q.group_id:
            group_fav[anchor_q.group_id] = f

    # 大题组：加载全部组内行（含未收藏的其他子题）
    group_ids = [q.group_id for q in anchors if q.group_id]
    group_rows: list[AIGeneratedQuestion] = []
    if group_ids:
        group_rows = (
            await db.execute(
                select(AIGeneratedQuestion).where(AIGeneratedQuestion.group_id.in_(group_ids))
            )
        ).scalars().all()

    # 独立题（锚点行无 group_id）
    standalone = [q for q in anchors if not q.group_id]

    # ── 批量加载来源原题元数据（年级/学期/科目），用于筛选（防 N+1）──
    all_rows = standalone + group_rows
    source_ids = {q.source_question_id for q in all_rows if q.source_question_id}
    meta_by_source: dict[int, dict] = {}
    if source_ids:
        meta_rows = await db.execute(
            select(Question.id, Assignment.grade, Assignment.subject, Assignment.semester)
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(Question.id.in_(source_ids))
        )
        meta_by_source = {r[0]: {"grade": r[1], "subject": r[2], "semester": r[3]} for r in meta_rows.all()}

    def _pass_filter(q: AIGeneratedQuestion) -> bool:
        """行级筛选：题型直接过滤（'未知'匹配 NULL/空串）；
        年级/学期/科目——上传题（自有 grade/subject/semester 三列齐全）按自有元数据逐列匹配，
        老题（三列全 NULL）回落 source 关联过滤，无 source 的老题在年级/科目/学期筛选下被排除
        （与 /ai-questions 列表语义一致）"""
        if question_type:
            if question_type == "未知":
                if q.question_type not in (None, ""):
                    return False
            elif q.question_type != question_type:
                return False
        if not (grade or subject or semester):
            return True
        # 上传题：自有元数据优先（上传时三列恒齐全，逐列匹配用户筛选值）
        if q.grade is not None:
            if grade and q.grade != grade:
                return False
            if subject and q.subject != subject:
                return False
            if semester and q.semester != semester:
                return False
            return True
        # 老题：回落 source 关联
        if not q.source_question_id:
            return False
        meta = meta_by_source.get(q.source_question_id)
        if meta is None:
            return False
        if grade and meta["grade"] != grade:
            return False
        if subject and meta["subject"] != subject:
            return False
        if semester and meta["semester"] != semester:
            return False
        return True

    def _meta_of(q: AIGeneratedQuestion) -> tuple[str | None, str | None]:
        """题目的年级/科目：上传题（自有三列齐全）用自有值，老题回落 source 关联作业元数据。
        与 _pass_filter 的取值逻辑保持一致。"""
        if q.grade is not None:
            return q.grade, q.subject
        meta = meta_by_source.get(q.source_question_id)
        if meta:
            return meta["grade"], meta["subject"]
        return None, None

    standalone_picked = [q for q in standalone if _pass_filter(q)]
    grouped_picked: dict[str, list[AIGeneratedQuestion]] = {}
    for q in group_rows:
        if _pass_filter(q):
            grouped_picked.setdefault(q.group_id, []).append(q)

    # 批量查询作答记录（防 N+1）
    shown = standalone_picked + [r for rows in grouped_picked.values() for r in rows]
    answers_by_qid: dict[int, list[AIQuestionAnswer]] = {}

    # 批量预签名原图 URL（上传转录的自有试题存的是存储标识；无 image_url 的行跳过，防 N+1 IO）。
    # 与 _build_error_entries 的预签名逻辑一致：dev 模式返回 /api/v1/files/ 本地路径，
    # 生产模式返回 MinIO 公网预签名 URL
    storage = StorageService()
    presigned_urls: dict[str, str] = {}
    all_urls = [q.image_url for q in shown if q.image_url]
    if all_urls:
        presigned_results = await asyncio.gather(
            *[storage.get_presigned_url(url) for url in all_urls],
            return_exceptions=True,
        )
        for url, presigned in zip(all_urls, presigned_results):
            if not isinstance(presigned, Exception):
                presigned_urls[url] = presigned
    if shown:
        ans_q = select(AIQuestionAnswer).where(
            AIQuestionAnswer.question_id.in_([q.id for q in shown]),
            AIQuestionAnswer.user_id == current_user.id,
        ).order_by(AIQuestionAnswer.answered_at)
        for a in (await db.execute(ans_q)).scalars().all():
            answers_by_qid.setdefault(a.question_id, []).append(a)

    def _build_answer_items(answers: list[AIQuestionAnswer]) -> list[dict]:
        """将作答记录转为 dict 列表（与 AnswerItem 字段一致）"""
        return [
            {
                "id": a.id,
                "is_correct": a.is_correct,
                "score": a.score,
                "full_score": a.full_score,
                "ai_feedback": a.ai_feedback,
                "selected_options": a.selected_options,
                "answer_text": a.answer_text,
                "answer_image_url": a.answer_image_url,
                "answered_at": a.answered_at,
            }
            for a in answers
        ]

    def _build_options(options_val) -> list[dict] | None:
        """安全构建选项列表（与 OptionItem 字段一致）"""
        if not options_val:
            return None
        return [{"label": o["label"], "text": o["text"]} for o in options_val]

    entries: list[dict] = []

    # ── 独立题 ──
    for q in standalone_picked:
        fav = fav_by_id[q.id]
        grade, subject = _meta_of(q)
        entries.append({
            "item_type": "ai",
            # 题目来源（自有试题=上传转录；其余视为 AI 生成），前端标签与筛选依据
            "source": "upload" if q.source == "upload" else "ai",
            "favorite_id": fav.id,
            "favorited_at": fav.created_at,
            "question": {
                "id": q.id,
                "source_question_id": q.source_question_id,
                "grade": grade,
                "subject": subject,
                "question_text": q.question_text,
                "answer": q.answer,
                "analysis": q.analysis,
                "question_type": q.question_type,
                "knowledge_point": q.knowledge_point,
                "difficulty": q.difficulty,
                "options": _build_options(q.options),
                "image_svg": q.image_svg,
                # 上传转录的自有试题原图（预签名后可直接访问）；AI 生成为空
                "image_url": presigned_urls.get(q.image_url, q.image_url),
                "user_answers": _build_answer_items(answers_by_qid.get(q.id, [])),
                "created_at": q.created_at,
                "is_big_question": False,
                "is_favorited": True,
            },
        })

    # ── 大题聚合 ──
    for gid, rows in grouped_picked.items():
        fav = group_fav.get(gid)
        if fav is None:
            continue  # 组内行均未直接对应锚点收藏（理论上不会发生），防御跳过
        rows.sort(key=lambda c: (c.sub_question_index or 0, c.id))

        child_items = [
            {
                "id": c.id,
                "sub_question_index": c.sub_question_index or 0,
                "question_text": c.question_text,
                "answer": c.answer,
                "analysis": c.analysis,
                "question_type": c.question_type,
                "knowledge_point": c.knowledge_point,
                "difficulty": c.difficulty,
                "options": _build_options(c.options),
                "image_svg": c.image_svg,
                # 上传转录的自有试题原图（预签名后可直接访问）；AI 生成为空
                "image_url": presigned_urls.get(c.image_url, c.image_url),
                "user_answers": _build_answer_items(answers_by_qid.get(c.id, [])),
                "created_at": c.created_at,
            }
            for c in rows
        ]

        # 得分率 = 各子题最新一次作答的得分合计 / 满分合计（与 /ai-questions 一致）
        total_score = 0.0
        total_full = 0.0
        for c in rows:
            ans = answers_by_qid.get(c.id, [])
            latest = ans[-1] if ans else None
            if latest:
                if latest.score is not None:
                    total_score += float(latest.score)
                if latest.full_score is not None:
                    total_full += float(latest.full_score)
        score_rate = round(total_score / total_full, 4) if total_full > 0 else None

        first = rows[0]
        grade, subject = _meta_of(first)
        entries.append({
            "item_type": "ai",
            # 题目来源（自有试题=上传转录；其余视为 AI 生成），前端标签与筛选依据
            "source": "upload" if first.source == "upload" else "ai",
            "favorite_id": fav.id,
            "favorited_at": fav.created_at,
            "question": {
                "id": None,  # 大题没有独立 id
                "source_question_id": first.source_question_id,
                "grade": grade,
                "subject": subject,
                "difficulty": first.difficulty,
                "created_at": max(c.created_at for c in rows),
                "is_big_question": True,
                "group_id": gid,
                "question_context": first.question_context or "",
                "context_image_svg": first.context_image_svg,
                "children": child_items,
                "total_count": len(rows),
                "score_rate": score_rate,
                "is_favorited": True,
            },
        })
    return entries
