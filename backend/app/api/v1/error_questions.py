import asyncio
import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, case
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.assignment import Assignment
from app.models.question import Question
from app.models.favorite import UserFavorite
from app.services.file_upload import StorageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/error-questions", tags=["error-questions"])

# 合法的题型白名单（与前端 QUESTION_TYPE_OPTIONS 保持一致）
_VALID_QUESTION_TYPES = frozenset({
    "单选题", "多选题", "选择题组", "填空题", "计算题", "应用题", "证明题",
    "简答题", "判断题", "阅读理解", "完形填空", "写作题", "作图题",
})


def _escape_like(value: str) -> str:
    """转义 LIKE 模式中的通配符 % 和 _，防止用户输入被当作通配符匹配。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# 子题文本前导的小问标记（如 "(1)"、"（2）"、"第①问"），去公共题干后剥掉，
# 避免与前端按序号渲染的 "(1)" 重复
_SUB_MARK_LEAD_RE = re.compile(r"^[\(\（]\s*\d+\s*[\)\）][、．.，,;；:：]?[\s]*|^第[一二三四五六七八九十百0-9]+[问題][、．.，,;；:：]?[\s]*|^[①②③④⑤⑥⑦⑧⑨⑩ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ][、．.，,;；:：]?[\s]*")

# 父题题干内的小问标记（用于从父题题干切出"公共题干"）。
# 前一个非空白字符必须是句末标点或文本开头，排除数学坐标等干扰
# （如"点A(1,0)"——(1 前是字母 A 而非标点，不算小问标记）
_SUB_MARK_IN_TEXT_RE = re.compile(r"[\(（]\s*\d+\s*[\)\）]|第[一二三四五六七八九十百0-9]+[问題]|[①②③④⑤⑥⑦⑧⑨⑩]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]")

_TERM_PUNCT = set("。；;：:！？…、，,?？．")


def _find_common_stem(parent_text: str) -> str:
    """从父题题干中切出公共题干：第一个小问标记（如"(1)"）之前的部分。

    历史数据里父题 question_text = "公共题干。(1)求证…(2)求点…"，
    子题 = "公共题干。(1)求证…"——即子题以公共题干为前缀复制。
    返回公共题干供子题去前缀；找不到小问标记时返回整个题干
    （此时子题若整体复制题干，走完整前缀匹配）。

    Args:
        parent_text: 父题（大题）完整题干文本

    Returns:
        公共题干文本
    """
    for m in _SUB_MARK_IN_TEXT_RE.finditer(parent_text):
        # 检查小问标记前一个非空白字符是否为句末标点或文本开头
        k = m.start() - 1
        while k >= 0 and parent_text[k].isspace():
            k -= 1
        if k < 0 or parent_text[k] in _TERM_PUNCT:
            return parent_text[: m.start()]
        # 前一个字符是字母/数字（如"点A(1,0)"）→ 不是小问标记，继续找
    return parent_text


def _strip_parent_prefix(child_text: str | None, parent_text: str | None) -> str | None:
    """子题题干去公共题干前缀：子题以父题公共题干开头时，剥掉公共部分。

    历史数据问题：旧版评分 prompt 要求每个小问复制整套大题干，导致子题
    question_text 形如"[公共题干](1)求证…"，展示时每个子题都重复整套题干。
    这里在聚合层兜底去重：从父题题干切出公共题干（第一个小问标记之前），
    子题文本以公共题干开头（去空白比较，兼容 LaTeX 空格差异）时只保留
    小问部分；再去掉前导小问标记，避免与前端 (idx+1) 序号重复。
    新数据（prompt 已改为子题只写小问自身）不受影响——子题文本不以公共
    题干开头，原样返回。

    Args:
        child_text: 子题题干文本
        parent_text: 父题（大题）完整题干文本

    Returns:
        去重后的子题文本；非复制场景原样返回
    """
    if not child_text or not parent_text:
        return child_text
    stem = _find_common_stem(parent_text)
    # 公共题干太短不做处理（避免误删仅开头相似的独立题目）
    stem_norm_len = sum(1 for ch in stem if not ch.isspace())
    if stem_norm_len < 8:
        return child_text

    # 去空白双指针对齐匹配（允许两侧空白位置不同，兼容 LaTeX 空格差异）
    i = j = 0
    n, m = len(child_text), len(stem)
    matched = 0
    while i < n and j < m:
        if child_text[i].isspace():
            i += 1
            continue
        if stem[j].isspace():
            j += 1
            continue
        if child_text[i] != stem[j]:
            break  # 字符不同：非逐字复制，保留原样
        i += 1
        j += 1
        matched += 1
    if j < m:
        return child_text  # 公共题干未匹配完，非完整复制
    if matched < 8:
        return child_text  # 匹配字符过少（防御，避免误删）
    # 复制成立：剥掉公共题干，再去掉前导小问标记（前端已有 (idx+1) 序号）
    rest = child_text[i:]
    rest = _SUB_MARK_LEAD_RE.sub("", rest).strip()
    # 剥空（子题文本与公共题干完全相同，无自身小问内容）→ 返回 None，
    # 前端 `child.question_text &&` 判空后不渲染，避免显示空行
    return rest or None


async def _build_child_item(child: Question, storage: StorageService, parent_text: str | None = None) -> dict:
    """将子题 ORM 对象转为前端需要的 dict 格式（异步版本，供外部调用）。"""
    return _build_child_item_sync(child, parent_text)


def _build_child_item_sync(child: Question, parent_text: str | None = None) -> dict:
    """将子题 ORM 对象转为前端需要的 dict 格式（同步版本，无 IO 依赖）。

    Args:
        child: 子题 ORM 对象
        parent_text: 父题完整题干文本（子题复制了完整题干时用于去重，可省略）
    """
    score_rate = (
        round(float(child.score) / float(child.full_score), 4)
        if child.score is not None and child.full_score
        else 0.0
    )
    return {
        "id": child.id,
        "sub_question_index": child.sub_question_index,
        "question_type": child.question_type,
        # 识别出的题干文本（含 LaTeX 公式）；历史数据子题可能复制了整套
        # 大题干，聚合时去重，只保留小问自身内容
        "question_text": _strip_parent_prefix(child.question_text, parent_text),
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
    # 构建叶子题（子题 + 独立题）的错题过滤条件
    conditions = [
        Assignment.creator_id == current_user.id,
        Question.score < Question.full_score,
    ]
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
    if search:
        conditions.append(Assignment.name.ilike(f"%{_escape_like(search)}%", escape="\\"))
    if score_rate_min is not None:
        # 使用乘法避免除法表达式潜在注入风险
        conditions.append(Question.score >= Question.full_score * score_rate_min)
    if score_rate_max is not None:
        conditions.append(Question.score <= Question.full_score * score_rate_max)

    # 当前用户已收藏的错题锚点 id 集合（供收藏按钮初始状态回显）
    fav_result = await db.execute(
        select(UserFavorite.question_id).where(
            UserFavorite.user_id == current_user.id,
            UserFavorite.item_type == "error",
        )
    )
    fav_set = set(fav_result.scalars().all())

    # ── 聚合单元：独立题以自身 id 为单元，子题以父题 id 为单元 ──
    # 一张父题卡片（含全部子题）= 一个单元。这样 total 与分页条数都和实际
    # 返回的卡片数一致；父题也不会因子题散落各页而被拆分/漏掉。
    unit_id_expr = case(
        (Question.parent_id.is_(None), Question.id),
        else_=Question.parent_id,
    ).label("unit_id")
    unit_query = (
        select(
            unit_id_expr,
            func.max(Question.created_at).label("latest_created"),
        )
        .join(Assignment, Question.assignment_id == Assignment.id)
        .where(*conditions)
        .group_by(unit_id_expr)
        .subquery()
    )

    # 单元总数（在数据库内聚合，不会全量拉数据到内存）
    total = (await db.execute(select(func.count()).select_from(unit_query))).scalar() or 0

    # ── SQL 层按单元分页（LIMIT/OFFSET 在数据库完成，避免全量拉取 OOM）──
    unit_ids = (
        await db.execute(
            select(unit_query.c.unit_id)
            .order_by(desc(unit_query.c.latest_created), desc(unit_query.c.unit_id))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    storage = StorageService()
    if not unit_ids:
        return {
            "items": [],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ── 加载本页单元对应的题目：父题容器（score 为 NULL）或独立题 ──
    unit_q_result = await db.execute(select(Question).where(Question.id.in_(unit_ids)))
    unit_q_map: dict[int, Question] = {q.id: q for q in unit_q_result.scalars().all()}
    parents_by_id: dict[int, Question] = {}
    standalone_rows: list[Question] = []
    for unit_id in unit_ids:
        q = unit_q_map.get(unit_id)
        if q is None:
            continue
        if q.score is None:
            parents_by_id[q.id] = q
        else:
            standalone_rows.append(q)

    # ── 父题：加载全部子题（含未错的，卡片需展示完整大题）──
    children_by_parent: dict[int, list[Question]] = {}
    if parents_by_id:
        all_children_query = (
            select(Question)
            .where(Question.parent_id.in_(parents_by_id))
            .order_by(Question.parent_id, Question.sub_question_index)
        )
        children_result = await db.execute(all_children_query)
        for child in children_result.scalars().all():
            children_by_parent.setdefault(child.parent_id, []).append(child)

    # ── 批量加载作业名 ──
    assignment_ids = {q.assignment_id for q in standalone_rows} | {
        p.assignment_id for p in parents_by_id.values()
    }
    assignment_names: dict[int, str] = {}
    if assignment_ids:
        assignment_result = await db.execute(
            select(Assignment.id, Assignment.name).where(Assignment.id.in_(assignment_ids))
        )
        assignment_names = {row[0]: row[1] for row in assignment_result.all()}

    # ── 批量预签名 URL（asyncio.gather 并发，避免 N+1 IO）──
    all_image_urls: list[str] = []
    for q in standalone_rows:
        if q.image_url:
            all_image_urls.append(q.image_url)
    for p in parents_by_id.values():
        if p.image_url:
            all_image_urls.append(p.image_url)

    presigned_urls: dict[str, str] = {}
    if all_image_urls:
        presigned_results = await asyncio.gather(
            *[storage.get_presigned_url(url) for url in all_image_urls],
            return_exceptions=True,
        )
        for url, presigned in zip(all_image_urls, presigned_results):
            if not isinstance(presigned, Exception):
                presigned_urls[url] = presigned

    # ── 构建独立题的返回项 ──
    items: list[dict] = []
    for question in standalone_rows:
        score_rate = (
            round(float(question.score) / float(question.full_score), 4)
            if question.score is not None and question.full_score
            else 0.0
        )
        items.append({
            "id": question.id,
            "assignment_id": question.assignment_id,
            "assignment_name": assignment_names.get(question.assignment_id, ""),
            "question_number": question.question_number,
            "question_type": question.question_type,
            "image_url": presigned_urls.get(question.image_url, question.image_url),
            "question_text": question.question_text,  # 识别出的题干文本（含 LaTeX 公式）
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
            "is_favorited": question.id in fav_set,
        })

    # ── 构建父题聚合项 ──
    for parent in parents_by_id.values():
        children = children_by_parent.get(parent.id, [])
        if not children:
            continue

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

        # 传入父题完整题干：历史数据子题复制了整套大题干时聚合层去重
        children_items = [_build_child_item_sync(child, parent.question_text) for child in children]

        items.append({
            "id": parent.id,
            "assignment_id": parent.assignment_id,
            "assignment_name": assignment_names.get(parent.assignment_id, ""),
            "question_number": parent.question_number,
            "question_type": parent.question_type,
            "image_url": presigned_urls.get(parent.image_url, parent.image_url),
            "score_rate": score_rate,
            "knowledge_points": parent.knowledge_points,
            "common_mistakes": parent.common_mistakes,
            "analysis_detail": parent.analysis_detail,
            "created_at": parent.created_at,
            "is_big_question": True,
            "children": children_items,
            "error_count": error_count,
            "total_count": len(children),
            "is_favorited": parent.id in fav_set,
        })

    # ── 按创建时间倒序排列（页内顺序稳定）──
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
