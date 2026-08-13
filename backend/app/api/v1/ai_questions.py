"""AI 生成题目 API——保存、列表、作答提交"""

import json
import time
import uuid
from collections import defaultdict
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, or_
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.ai_question import AIGeneratedQuestion, AIQuestionAnswer
from app.models.favorite import UserFavorite
from app.schemas.ai_question import (
    SaveAIQuestionRequest, AIQuestionResponse, AnswerItem,
    OptionItem, SubmitAnswerResponse, AIQuestionListItem, AISubQuestionResponse,
)

router = APIRouter(prefix="/ai-questions", tags=["ai-questions"])


class SaveBigQuestionRequest(BaseModel):
    """保存大题（含多个子题）的请求"""
    source_question_id: int | None = None
    question_context: str = ""  # 大题背景材料
    question_context_image_svg: str | None = None  # 背景材料配图（纯 SVG 代码）
    difficulty: str = "medium"
    # 每项含 question_text, answer, question_type, knowledge_point, options, full_score, image_svg
    # 可选 existing_question_id：作答时已创建的题目记录 id，用于复用并保留作答
    sub_questions: list[dict]


class AISubQuestionContentUpdate(BaseModel):
    """AI 大题子题的内容更新项（只允许内容字段，不触碰难度/配图/作答）"""
    id: int  # 子题记录 id
    question_text: str | None = None  # 题干文本（含 $...$ LaTeX）
    answer: str | None = None  # 正确答案
    analysis: str | None = None  # 解析
    options: list[OptionItem] | None = None  # 选项（选择题；None 表示不修改，[] 表示清空）


class AIQuestionContentUpdate(BaseModel):
    """AI 题内容更新请求：独立题更新自身字段；大题按 id 批量更新子题并支持更新背景材料。

    均只更新显式传入的字段（"" 为合法清空值）。
    """
    question_text: str | None = None
    answer: str | None = None
    analysis: str | None = None
    options: list[OptionItem] | None = None  # 选项（选择题；None 表示不修改，[] 表示清空）
    question_context: str | None = None  # 大题背景材料（仅大题生效）
    children: list[AISubQuestionContentUpdate] | None = None  # 大题子题批量更新


@router.post("/big-question", status_code=status.HTTP_201_CREATED)
async def save_big_question(
    body: SaveBigQuestionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存AI生成的大题（含所有子题）到繁星驱动。

    若子题在作答时已创建了题目记录（existing_question_id），则复用该记录
    并归入大题分组，从而保留已有作答（避免在繁星驱动重复显示且显示“未作答”）；
    否则新建记录。
    """
    group_id = str(uuid.uuid4())  # 生成分组ID，同组子题共享
    saved_ids = []
    for idx, sq in enumerate(body.sub_questions):
        existing_id = sq.get("existing_question_id")
        reused = None
        if existing_id:
            candidate = await db.get(AIGeneratedQuestion, existing_id)
            # 复用条件：记录属于当前用户即可（A3-6 幂等修复）。
            # 原实现额外要求 group_id 为空——二次提交同一批 existing_question_id 时
            # group_id 已被占用 → 复用失败 → 重复生成整套子题。
            # 已归组只说明此前保存过，应复用该记录（保留作答）并迁移到新分组。
            if candidate and candidate.user_id == current_user.id:
                reused = candidate

        if reused is not None:
            # 复用已有记录（保留其作答），补充大题分组信息
            reused.group_id = group_id
            reused.sub_question_index = idx
            reused.question_context = body.question_context or None
            reused.context_image_svg = body.question_context_image_svg or None
            reused.difficulty = body.difficulty
            reused.source_question_id = body.source_question_id
            if sq.get("analysis"):
                reused.analysis = sq.get("analysis")
            # 键存在即覆盖（含空串清空配图，A3-6）：原实现仅在 image_svg 为真值时
            # 覆盖，用户清空配图（传空字符串）时不生效，旧配图残留
            if "image_svg" in sq:
                reused.image_svg = sq.get("image_svg") or None
            saved_ids.append(reused.id)
        else:
            q = AIGeneratedQuestion(
                user_id=current_user.id,
                source_question_id=body.source_question_id,
                question_text=sq.get("question_text", ""),
                answer=sq.get("answer", ""),
                analysis=sq.get("analysis"),
                question_type=sq.get("question_type"),
                knowledge_point=sq.get("knowledge_point"),
                difficulty=body.difficulty,
                options=sq.get("options"),
                image_svg=sq.get("image_svg") or None,
                context_image_svg=body.question_context_image_svg or None,
                # 大题分组字段
                group_id=group_id,
                sub_question_index=idx,
                question_context=body.question_context or None,
            )
            db.add(q)
            await db.flush()
            await db.refresh(q)
            saved_ids.append(q.id)

    await db.commit()
    return {"ids": saved_ids, "count": len(saved_ids), "message": f"已保存 {len(saved_ids)} 道子题到繁星驱动"}


@router.post("", status_code=status.HTTP_201_CREATED)
async def save_ai_question(
    body: SaveAIQuestionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存AI生成的题目"""
    q = AIGeneratedQuestion(
        user_id=current_user.id,
        source_question_id=body.source_question_id,
        question_text=body.question_text,
        answer=body.answer,
        analysis=body.analysis,
        question_type=body.question_type,
        knowledge_point=body.knowledge_point,
        difficulty=body.difficulty,
        options=[o.model_dump() for o in body.options] if body.options else None,
        image_svg=body.image_svg or None,
    )
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return {"id": q.id}


@router.get("")
async def list_ai_questions(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    grade: str | None = Query(None),
    subject: str | None = Query(None),
    semester: str | None = Query(None),
    question_type: str | None = Query(None),
    difficulty: str | None = Query(None),
    score_rate_min: float | None = Query(None, ge=0, le=1),
    score_rate_max: float | None = Query(None, ge=0, le=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出AI生成题目（繁星驱动板块）——支持大题分组聚合。
    score_rate 是聚合值（大题按最近一次作答累计计算），无法用 SQL 条件过滤，
    必须在 Python 侧分组构建 items 之后再过滤。"""
    conditions = [AIGeneratedQuestion.user_id == current_user.id]
    # 繁星驱动只展示 AI 生成的题（source 为 NULL 或 'ai'）。
    # 上传转录的题（source='upload'）与 AI 题共用本表并自动收藏，
    # 但应只出现在收藏页——不按 source 过滤会让用户上传的题混入繁星驱动
    conditions.append(
        or_(
            AIGeneratedQuestion.source.is_(None),
            AIGeneratedQuestion.source == "ai",
        )
    )

    # 当前用户已收藏的 AI 题锚点 id 集合（供收藏按钮初始状态回显）
    fav_result = await db.execute(
        select(UserFavorite.question_id).where(
            UserFavorite.user_id == current_user.id,
            UserFavorite.item_type == "ai",
        )
    )
    fav_set = set(fav_result.scalars().all())

    # 难度直接过滤
    if difficulty:
        conditions.append(AIGeneratedQuestion.difficulty == difficulty)

    # 通过关联查询原题来过滤年级/科目/学期
    if grade or subject or semester or question_type:
        from app.models.question import Question
        from app.models.assignment import Assignment
        # 年级/科目/学期：两个 IN 子查询 OR（不能用加 JOIN 的 or_ 条件——
        # inner join 会连带过滤掉没有 source 关联的"上传题"行）：
        # - 上传题：自有 grade/subject/semester 元数据逐列匹配
        # - 老题：三列全 NULL，回落 source 关联原作业的元数据
        if grade or subject or semester:
            own_cond = []
            if grade:
                own_cond.append(AIGeneratedQuestion.grade == grade)
            if subject:
                own_cond.append(AIGeneratedQuestion.subject == subject)
            if semester:
                own_cond.append(AIGeneratedQuestion.semester == semester)
            own_subq = select(AIGeneratedQuestion.id).where(*own_cond)

            src_subq = (
                select(AIGeneratedQuestion.id)
                .join(Question, AIGeneratedQuestion.source_question_id == Question.id)
                .join(Assignment, Question.assignment_id == Assignment.id)
                .where(
                    AIGeneratedQuestion.grade.is_(None),
                    AIGeneratedQuestion.subject.is_(None),
                    AIGeneratedQuestion.semester.is_(None),
                )
            )
            if grade:
                src_subq = src_subq.where(Assignment.grade == grade)
            if subject:
                src_subq = src_subq.where(Assignment.subject == subject)
            if semester:
                src_subq = src_subq.where(Assignment.semester == semester)
            conditions.append(or_(
                AIGeneratedQuestion.id.in_(own_subq),
                AIGeneratedQuestion.id.in_(src_subq),
            ))
        if question_type:
            subq = select(AIGeneratedQuestion.id)
            if question_type == "未知":
                subq = subq.where(
                    (AIGeneratedQuestion.question_type == None) |
                    (AIGeneratedQuestion.question_type == "")
                )
            else:
                subq = subq.where(AIGeneratedQuestion.question_type == question_type)
            conditions.append(AIGeneratedQuestion.id.in_(subq))

    # 全量查询（不加分页，在 Python 侧分组后再分页）
    list_q = (
        select(AIGeneratedQuestion)
        .where(*conditions)
        .order_by(desc(AIGeneratedQuestion.created_at))
    )
    rows = (await db.execute(list_q)).scalars().all()

    # ── 分离独立题和分组题 ──
    grouped: dict[str, list[AIGeneratedQuestion]] = {}
    standalone_rows: list[AIGeneratedQuestion] = []

    for q in rows:
        if q.group_id:
            grouped.setdefault(q.group_id, []).append(q)
        else:
            standalone_rows.append(q)

    # ── 批量预签名原图 URL（上传转录的自有试题存的是存储标识；无 image_url 的行跳过，防 N+1 IO）──
    # 与 favorites._build_ai_entries 逻辑一致：dev 模式返回 /api/v1/files/ 本地路径，
    # 生产模式返回 MinIO 公网预签名 URL
    from app.services.file_upload import StorageService
    storage = StorageService()
    presigned_urls: dict[str, str] = {}
    all_urls = [q.image_url for q in rows if q.image_url]
    if all_urls:
        import asyncio as _io_asyncio
        presigned_results = await _io_asyncio.gather(
            *[storage.get_presigned_url(url) for url in all_urls],
            return_exceptions=True,
        )
        for url, presigned in zip(all_urls, presigned_results):
            if not isinstance(presigned, Exception):
                presigned_urls[url] = presigned

    # ── 批量查询所有作答记录（避免 N+1） ──
    all_question_ids = [q.id for q in rows]
    answers_by_qid: dict[int, list[AIQuestionAnswer]] = {}
    if all_question_ids:
        ans_q = select(AIQuestionAnswer).where(
            AIQuestionAnswer.question_id.in_(all_question_ids),
            AIQuestionAnswer.user_id == current_user.id,
        ).order_by(AIQuestionAnswer.answered_at)
        ans_rows = (await db.execute(ans_q)).scalars().all()
        for a in ans_rows:
            answers_by_qid.setdefault(a.question_id, []).append(a)

    def _build_answer_items(answers: list[AIQuestionAnswer]) -> list[AnswerItem]:
        """将作答记录转为 AnswerItem 列表"""
        return [
            AnswerItem(
                id=a.id,
                is_correct=a.is_correct,
                score=a.score,
                full_score=a.full_score,
                ai_feedback=a.ai_feedback,
                selected_options=a.selected_options,
                answer_text=a.answer_text,
                answer_image_url=a.answer_image_url,
                answered_at=a.answered_at,
            )
            for a in answers
        ]

    def _build_options(options_val) -> list[OptionItem] | None:
        """安全构建选项列表"""
        if not options_val:
            return None
        return [OptionItem(**o) for o in options_val]

    items: list[dict] = []

    # ── 独立题 ──
    for q in standalone_rows:
        ans = answers_by_qid.get(q.id, [])
        items.append({
            "id": q.id,
            "source_question_id": q.source_question_id,
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
            "user_answers": _build_answer_items(ans),
            "created_at": q.created_at,
            "is_big_question": False,
            "is_favorited": q.id in fav_set,
        })

    # ── 大题聚合 ──
    for gid, children in grouped.items():
        children.sort(key=lambda c: c.sub_question_index or 0)

        child_items: list[dict] = []
        total_score = 0.0
        total_full = 0.0

        for c in children:
            ans = answers_by_qid.get(c.id, [])
            latest = ans[-1] if ans else None
            if latest:
                if latest.score is not None:
                    total_score += float(latest.score)
                if latest.full_score is not None:
                    total_full += float(latest.full_score)

            child_items.append({
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
                "user_answers": _build_answer_items(ans),
                "created_at": c.created_at,
            })

        score_rate = round(total_score / total_full, 4) if total_full > 0 else None
        first = children[0]
        max_created = max(c.created_at for c in children)

        items.append({
            "id": None,  # 大题没有独立 id
            "source_question_id": first.source_question_id,
            "difficulty": first.difficulty,
            "created_at": max_created,
            "is_big_question": True,
            "group_id": gid,
            "question_context": first.question_context or "",
            "context_image_svg": first.context_image_svg,
            "children": child_items,
            "total_count": len(children),
            "score_rate": score_rate,
            # 大题以组内第一子题（sub_question_index 最小）为收藏锚点
            "is_favorited": children[0].id in fav_set,
        })

    # ── 得分率筛选（聚合后过滤；无 score_rate 的项不满足任何范围条件） ──
    if score_rate_min is not None or score_rate_max is not None:
        items = [
            it for it in items
            if it.get("score_rate") is not None
            and (score_rate_min is None or it["score_rate"] >= score_rate_min)
            and (score_rate_max is None or it["score_rate"] <= score_rate_max)
        ]

    # ── 按创建时间降序 + 分页 ──
    items.sort(key=lambda x: x.get("created_at") or datetime.min, reverse=True)
    total = len(items)
    page_items = items[(page - 1) * page_size : page * page_size]

    # ── 转为 Pydantic 模型 ──
    result = []
    for it in page_items:
        if it.get("is_big_question"):
            result.append(AIQuestionListItem(
                is_big_question=True,
                group_id=it["group_id"],
                question_context=it["question_context"],
                context_image_svg=it["context_image_svg"],
                source_question_id=it["source_question_id"],
                difficulty=it["difficulty"],
                created_at=it["created_at"],
                children=[AISubQuestionResponse(**c) for c in it["children"]],
                total_count=it["total_count"],
                score_rate=it["score_rate"],
                is_favorited=it["is_favorited"],
            ))
        else:
            result.append(AIQuestionListItem(
                id=it["id"],
                source_question_id=it["source_question_id"],
                question_text=it["question_text"],
                answer=it["answer"],
                analysis=it["analysis"],
                question_type=it["question_type"],
                knowledge_point=it["knowledge_point"],
                difficulty=it["difficulty"],
                options=it["options"],
                image_svg=it["image_svg"],
                image_url=it["image_url"],
                user_answers=it["user_answers"],
                created_at=it["created_at"],
                is_big_question=False,
                is_favorited=it["is_favorited"],
            ))

    return {"items": result, "total": total}


@router.get("/{question_id}")
async def get_ai_question(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个AI题目详情"""
    q = await db.get(AIGeneratedQuestion, question_id)
    if not q or q.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="题目不存在")

    ans_q = select(AIQuestionAnswer).where(
        AIQuestionAnswer.question_id == q.id,
        AIQuestionAnswer.user_id == current_user.id,
    ).order_by(AIQuestionAnswer.answered_at)
    ans_rows = (await db.execute(ans_q)).scalars().all()

    return AIQuestionResponse(
        id=q.id,
        source_question_id=q.source_question_id,
        question_text=q.question_text,
        answer=q.answer,
        analysis=q.analysis,
        question_type=q.question_type,
        knowledge_point=q.knowledge_point,
        difficulty=q.difficulty,
        options=[OptionItem(**o) for o in q.options] if q.options else None,
        image_svg=q.image_svg,
        context_image_svg=q.context_image_svg,
        user_answers=[
            AnswerItem(
                id=a.id,
                is_correct=a.is_correct,
                score=a.score,
                full_score=a.full_score,
                ai_feedback=a.ai_feedback,
                selected_options=a.selected_options,
                answer_text=a.answer_text,
                answer_image_url=a.answer_image_url,
                answered_at=a.answered_at,
            )
            for a in ans_rows
        ],
        created_at=q.created_at,
    )


@router.post("/submit")
async def submit_with_question(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    source_question_id: int = Form(0),
    question_text: str = Form(...),
    answer: str = Form(""),
    analysis: str = Form(""),
    question_type: str = Form(""),
    knowledge_point: str = Form(""),
    difficulty: str = Form("medium"),
    options_json: str = Form(""),
    image_svg: str | None = Form(None),
    selected_options: str | None = Form(None),
    answer_text: str | None = Form(None),
    answer_image: UploadFile | None = File(None),
):
    """提交AI题目的作答（如题目不存在则先创建）"""
    # 创建或获取题目
    options_list = None
    if options_json:
        try:
            options_list = json.loads(options_json)
        except (json.JSONDecodeError, TypeError):
            pass

    # 创建题目
    q = AIGeneratedQuestion(
        user_id=current_user.id,
        source_question_id=source_question_id if source_question_id else None,
        question_text=question_text,
        answer=answer,
        analysis=analysis or None,
        question_type=question_type or None,
        knowledge_point=knowledge_point or None,
        difficulty=difficulty or "medium",
        options=options_list,
        image_svg=image_svg or None,
    )
    db.add(q)
    await db.flush()

    # 处理图片上传
    # json.loads 容错：selected_options 非法 JSON 时视为未选择，避免 500
    try:
        selected = json.loads(selected_options) if selected_options else None
    except (json.JSONDecodeError, TypeError):
        selected = None
    image_url = None
    if answer_image:
        # 复用统一的文件校验（扩展名/魔数/大小），与作业上传保持一致
        from app.api.v1.assignments import _validate_and_read_file
        content = await _validate_and_read_file(answer_image)
        from app.services.file_upload import StorageService
        storage = StorageService()
        image_url = await storage.save_question_image(
            content, current_user.id, 0
        )

    # 获取原题满分
    source_full_score = 10.0  # 默认值
    if source_question_id:
        from app.models.question import Question as SourceQuestion
        sq_result = await db.execute(select(SourceQuestion).where(SourceQuestion.id == source_question_id))
        source_q = sq_result.scalar_one_or_none()
        if source_q and source_q.full_score is not None:
            source_full_score = source_q.full_score

    # 单选题/多选题：直接判对错
    is_choice = question_type and ("选" in question_type)
    is_multi = question_type and ("多选" in question_type)
    is_correct = None
    score_val = None
    feedback = None

    if is_choice and answer and selected:
        correct_upper = answer.strip().upper()
        user_set = set(s.strip().upper() for s in selected)

        if is_multi:
            correct_set = set(c.strip().upper() for c in correct_upper.split(","))
            wrong_selected = user_set - correct_set
            if wrong_selected:
                # 选了错误选项 → 0分
                score_val = 0.0
                is_correct = False
                feedback = f"选入了错误选项 {','.join(sorted(wrong_selected))}，正确答案是 {correct_upper}"
            else:
                correct_selected = user_set & correct_set
                if correct_selected == correct_set:
                    score_val = float(source_full_score)
                    is_correct = True
                    feedback = "回答正确！"
                else:
                    # 部分正确：按比例给分
                    ratio = len(correct_selected) / len(correct_set)
                    score_val = round(float(source_full_score) * ratio, 1)
                    is_correct = False
                    missing = correct_set - correct_selected
                    feedback = f"部分正确（漏选 {','.join(sorted(missing))}），得分 {score_val}/{source_full_score}"
        else:
            # 单选题：必须完全匹配
            user_sel = "".join(sorted(user_set))
            is_correct = (user_sel == correct_upper)
            score_val = float(source_full_score) if is_correct else 0.0
            feedback = "回答正确！" if is_correct else f"回答错误，正确答案是 {correct_upper}"
    elif not is_choice and (answer_text or image_url):
        from app.services.similar_generator import SimilarGenerator
        from app.services.personality_service import load_grading_directive
        generator = SimilarGenerator()
        user_content = answer_text or ""
        if image_url:
            user_content += " [上传了答案图片]"
        # 题目带 SVG 配图时一并传给评分模型（SVG 是文本，可直接拼入）
        grading_text = question_text
        if image_svg:
            grading_text += f"\n\n[题目配图SVG]\n{image_svg}"
        grading = await generator.grade_answer(
            question_text=grading_text,
            correct_answer=answer,
            user_answer=user_content,
            knowledge_point=knowledge_point or "",
            full_score=float(source_full_score),
            personality_directive=await load_grading_directive(db, current_user.id),
        )
        score_val = grading["score"]
        is_correct = grading["is_correct"]
        feedback = grading["feedback"]

    answer_record = AIQuestionAnswer(
        question_id=q.id,
        user_id=current_user.id,
        selected_options=selected,
        answer_text=answer_text,
        answer_image_url=image_url,
        is_correct=is_correct,
        score=score_val,
        full_score=float(source_full_score),
        ai_feedback=feedback,
        correct_answer_revealed=True,
    )
    db.add(answer_record)
    await db.commit()
    await db.refresh(answer_record)

    return SubmitAnswerResponse(
        question_id=q.id,
        is_correct=is_correct or False,
        score=score_val or 0,
        full_score=float(source_full_score),
        feedback=feedback or "",
        correct_answer=answer,
        selected_options=selected,
        answer_text=answer_text,
        answer_image_url=image_url,
    )


@router.post("/{question_id}/submit-answer")
async def submit_answer(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    selected_options: str | None = Form(None),
    answer_text: str | None = Form(None),
    answer_image: UploadFile | None = File(None),
):
    """提交AI题目的作答并评分"""
    q = await db.get(AIGeneratedQuestion, question_id)
    if not q or q.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="题目不存在")

    # json.loads 容错：selected_options 非法 JSON 时视为未选择，避免 500
    try:
        options_list = json.loads(selected_options) if selected_options else None
    except (json.JSONDecodeError, TypeError):
        options_list = None
    image_url = None

    if answer_image:
        # 复用统一的文件校验（扩展名/魔数/大小），与作业上传保持一致
        from app.api.v1.assignments import _validate_and_read_file
        content = await _validate_and_read_file(answer_image)
        from app.services.file_upload import StorageService
        storage = StorageService()
        image_url = await storage.save_question_image(
            content, current_user.id, 0  # assignment_id=0 for AI questions
        )

    # 获取原题满分
    source_full_score = 10.0
    if q.source_question_id:
        from app.models.question import Question as SourceQuestion
        sq_result = await db.execute(select(SourceQuestion).where(SourceQuestion.id == q.source_question_id))
        source_q = sq_result.scalar_one_or_none()
        if source_q and source_q.full_score is not None:
            source_full_score = source_q.full_score

    # 单选题/多选题：直接判断对错
    is_choice = q.question_type and ("选" in q.question_type)
    is_multi = q.question_type and ("多选" in q.question_type)
    is_correct = None
    score = None
    feedback = None

    if is_choice and q.answer and options_list:
        correct_upper = q.answer.strip().upper()
        user_set = set(s.strip().upper() for s in options_list)

        if is_multi:
            correct_set = set(c.strip().upper() for c in correct_upper.split(","))
            wrong_selected = user_set - correct_set
            if wrong_selected:
                score = 0.0
                is_correct = False
                feedback = f"选入了错误选项 {','.join(sorted(wrong_selected))}，正确答案是 {correct_upper}"
            else:
                correct_selected = user_set & correct_set
                if correct_selected == correct_set:
                    score = float(source_full_score)
                    is_correct = True
                    feedback = "回答正确！"
                else:
                    ratio = len(correct_selected) / len(correct_set)
                    score = round(float(source_full_score) * ratio, 1)
                    is_correct = False
                    missing = correct_set - correct_selected
                    feedback = f"部分正确（漏选 {','.join(sorted(missing))}），得分 {score}/{source_full_score}"
        else:
            user_sel = "".join(sorted(user_set))
            is_correct = (user_sel == correct_upper)
            score = float(source_full_score) if is_correct else 0.0
            feedback = "回答正确！" if is_correct else f"回答错误，正确答案是 {correct_upper}"
    elif not is_choice and (answer_text or image_url):
        # 填空/解答题：AI 评分
        from app.services.similar_generator import SimilarGenerator
        from app.services.personality_service import load_grading_directive
        generator = SimilarGenerator()
        user_content = answer_text or ""
        if image_url:
            user_content += f" [上传了答案图片]"
        # 题目带 SVG 配图时一并传给评分模型（SVG 是文本，可直接拼入）
        grading_text = q.question_text
        if q.image_svg:
            grading_text += f"\n\n[题目配图SVG]\n{q.image_svg}"
        result = await generator.grade_answer(
            question_text=grading_text,
            correct_answer=q.answer,
            user_answer=user_content,
            knowledge_point=q.knowledge_point or "",
            full_score=float(source_full_score),
            personality_directive=await load_grading_directive(db, current_user.id),
        )
        score = result["score"]
        is_correct = result["is_correct"]
        feedback = result["feedback"]

    answer = AIQuestionAnswer(
        question_id=question_id,
        user_id=current_user.id,
        selected_options=options_list,
        answer_text=answer_text,
        answer_image_url=image_url,
        is_correct=is_correct,
        score=score,
        full_score=float(source_full_score),
        ai_feedback=feedback,
        correct_answer_revealed=True,
    )
    db.add(answer)
    await db.commit()
    await db.refresh(answer)

    return SubmitAnswerResponse(
        question_id=question_id,
        is_correct=is_correct or False,
        score=score or 0,
        full_score=float(source_full_score),
        feedback=feedback or "",
        correct_answer=q.answer,
        selected_options=options_list,
        answer_text=answer_text,
        answer_image_url=image_url,
    )


# ── AI 题目同类题生成（复用 similar_generator，数据源为 AIGeneratedQuestion）──

import asyncio as _ai_asyncio

# 同类题生成任务状态缓存：TTLCache 限容量 + 限 TTL，避免普通 dict 无界增长
from cachetools import TTLCache
from app.core.config import get_settings as _get_settings
_ai_similar_cache: TTLCache = TTLCache(maxsize=200, ttl=1800)


async def _set_ai_similar_cache(ai_question_id: int, value: dict) -> None:
    """写 AI 题同类题任务状态：DEV 写进程内 TTLCache；生产写 Redis（跨 worker 共享）。

    生产多 worker 部署时任务跑在 worker A、轮询打到 worker B，进程内缓存互相
    不可见，必须持久化到 Redis（见 services/redis_state.py）。
    """
    _ai_similar_cache[ai_question_id] = value
    if not _get_settings().DEV_MODE:
        from app.services.redis_state import redis_state_set
        await redis_state_set(f"ai_similar:{ai_question_id}", value)


async def _get_ai_similar_cache(ai_question_id: int) -> dict | None:
    """读 AI 题同类题任务状态（生产模式从 Redis 读，与 _set_ai_similar_cache 对应）。"""
    if _get_settings().DEV_MODE:
        return _ai_similar_cache.get(ai_question_id)
    from app.services.redis_state import redis_state_get
    return await redis_state_get(f"ai_similar:{ai_question_id}")


async def _run_ai_similar_generation(ai_question_id: int):
    """后台执行 AI 题目的同类题生成——逐题生成，每完成 1 题就更新缓存"""
    import logging
    _logger = logging.getLogger(__name__)
    try:
        # 任务启动即释放并发占位锁（锁只防"投递→启动"窗口的重复触发；
        # 运行期间由 existing 状态的 pending/processing 拦截新任务）
        if not _get_settings().DEV_MODE:
            from app.services.redis_state import redis_state_del
            await redis_state_del(f"ai_similar:{ai_question_id}:lock")
        from sqlalchemy import select as _select
        from app.db.session import async_session_factory
        from app.services.similar_generator import SimilarGenerator

        async with async_session_factory() as _db:
            _result = await _db.execute(
                _select(AIGeneratedQuestion).where(AIGeneratedQuestion.id == ai_question_id)
            )
            _ai_q = _result.scalar_one_or_none()
            if not _ai_q:
                await _set_ai_similar_cache(ai_question_id, {"status": "failed", "error": "题目不存在"})
                return

            generator = SimilarGenerator()
            difficulties = ["easy", "medium", "hard"]
            all_results = []
            SINGLE_TIMEOUT = 90

            await _set_ai_similar_cache(ai_question_id, {"status": "processing", "result": all_results})
            for diff in difficulties:
                try:
                    sq = await _ai_asyncio.wait_for(
                        generator.generate_one(
                            knowledge_points=[_ai_q.knowledge_point] if _ai_q.knowledge_point else None,
                            student_answer=None,
                            correct_answer=_ai_q.answer,
                            analysis_detail=None,
                            question_type=_ai_q.question_type,
                            difficulty=diff,
                            exclude_text=" | ".join(r.get("question_text", "")[:60] for r in all_results),
                        ),
                        timeout=SINGLE_TIMEOUT,
                    )
                except (_ai_asyncio.TimeoutError, Exception) as _gen_exc:
                    if isinstance(_gen_exc, _ai_asyncio.TimeoutError):
                        _logger.error("AI similar generation timeout for q %d difficulty %s", ai_question_id, diff)
                    else:
                        _logger.error("AI similar generation failed for q %d difficulty %s: %s", ai_question_id, diff, _gen_exc)
                    sq = None

                if sq:
                    all_results.append({
                        "id": len(all_results),
                        "question_text": sq.question_text,
                        "answer": sq.answer,
                        "analysis": sq.analysis,
                        "knowledge_point": sq.knowledge_point,
                        "difficulty": sq.difficulty,
                        "question_type": sq.question_type,
                        "options": sq.options,
                        "image_svg": sq.image_svg,
                    })
                else:
                    all_results.append({
                        "id": len(all_results),
                        "question_text": "生成失败，请点击换一题",
                        "answer": "",
                        "analysis": "",
                        "knowledge_point": _ai_q.knowledge_point or "",
                        "difficulty": diff,
                        "question_type": _ai_q.question_type or "",
                        "options": [],
                    })
                await _set_ai_similar_cache(ai_question_id, {"status": "processing", "result": list(all_results)})

        await _set_ai_similar_cache(ai_question_id, {"status": "completed", "result": all_results})
    except Exception as _exc:
        await _set_ai_similar_cache(ai_question_id, {"status": "failed", "error": str(_exc)})


@router.post("/{question_id}/similar", status_code=status.HTTP_202_ACCEPTED)
async def generate_ai_similar(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为 AI 题目创建同类题生成任务"""
    q = await db.get(AIGeneratedQuestion, question_id)
    if not q or q.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="题目不存在")

    existing = await _get_ai_similar_cache(question_id)
    if existing and existing["status"] in ("pending", "processing"):
        return {"status": existing["status"], "message": "已有同类题生成任务进行中"}

    if _get_settings().DEV_MODE:
        await _set_ai_similar_cache(question_id, {"status": "pending"})
        # 用 run_async_in_background 持有任务引用，防止 create_task 的裸任务被 GC 回收
        #（回收后缓存永久停在 pending，前端轮询永不返回）
        from app.tasks.dev_runner import run_async_in_background
        run_async_in_background(_run_ai_similar_generation(question_id))
    else:
        # 生产模式：投递 Celery 由 worker 执行（状态写 Redis，见 _set_ai_similar_cache）。
        # SET NX 原子占位：堵住并发请求同时通过上方 existing 检查的竞态窗口。
        # 注意锁 key 与状态 key 分离（锁 = "ai_similar:{id}:lock"，状态 = "ai_similar:{id}"）：
        # 若共用同一 key，任务完成后状态仍保留 completed（TTL 1800s），
        # SET NX 永远失败 → 用户 30 分钟内无法再次生成。任务启动时会删锁。
        from app.services.redis_state import redis_state_setnx
        if not await redis_state_setnx(
            f"ai_similar:{question_id}:lock", {"status": "pending"}, ttl=_AI_SIMILAR_LOCK_TTL
        ):
            return {"status": "pending", "message": "已有同类题生成任务进行中"}
        from app.tasks.analysis_tasks import generate_ai_similar_questions
        generate_ai_similar_questions.delay(question_id)
    return {"status": "pending", "message": "同类题生成任务已创建"}


@router.get("/{question_id}/similar-result")
async def get_ai_similar_result(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 AI 题目同类题生成结果"""
    q = await db.get(AIGeneratedQuestion, question_id)
    if not q or q.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="题目不存在")

    cached = await _get_ai_similar_cache(question_id)
    if not cached:
        return {"status": "not_found"}

    result_data = cached.get("result", [])
    # 换一题任务状态（pending/processing/completed/failed），前端轮询消费
    replace = cached.get("replace")
    if cached["status"] == "completed":
        return {"status": "completed", "similar_questions": result_data, "replace": replace}
    elif cached["status"] == "failed":
        return {"status": "failed", "error": cached.get("error", "生成失败"), "replace": replace}
    else:
        return {"status": cached["status"], "similar_questions": result_data, "replace": replace}


# similar-single 接口频率限制：每用户每小时最多 30 次（LLM 调用成本较高，与
# questions.py 的错题换题接口保持一致）
_ai_similar_single_timestamps: dict[int, list[float]] = defaultdict(list)
_AI_SIMILAR_SINGLE_MAX_PER_HOUR = 30
_AI_SIMILAR_SINGLE_RATE_WINDOW = 3600

# 生产模式并发占位锁 TTL（锁 key 为 "ai_similar:{question_id}:lock"，与状态 key
# 分离，避免任务完成后状态仍保留、SET NX 永远失败导致 30 分钟内无法再次生成）：
# 锁在任务启动时释放（redis_state_del），TTL 只兜底覆盖"投递→启动"的窗口。
_AI_SIMILAR_LOCK_TTL = 900   # 批量生成任务软超时 900s
_AI_REPLACE_LOCK_TTL = 300   # 单题替换任务软超时 300s


class AIReplaceRequest(BaseModel):
    difficulty: str = "medium"  # easy | medium | hard
    index: int = -1  # 要替换的卡片下标；不指定时传 -1


async def _run_ai_single_replace(ai_question_id: int, index: int, difficulty: str):
    """后台执行 AI 题目的单题替换（换一题）：生成 1 道同类题，写回缓存 replace 任务结果。

    失败时不破坏已有结果，只把 replace.status 置为 failed 供前端提示。
    """
    import logging
    _logger = logging.getLogger(__name__)
    try:
        # 任务启动即释放并发占位锁（与批量生成共用同一把锁；见 _AI_SIMILAR_LOCK_TTL 注释）
        if not _get_settings().DEV_MODE:
            from app.services.redis_state import redis_state_del
            await redis_state_del(f"ai_similar:{ai_question_id}:lock")
        from sqlalchemy import select as _select
        from app.db.session import async_session_factory
        from app.services.similar_generator import SimilarGenerator

        async with async_session_factory() as _db:
            _result = await _db.execute(
                _select(AIGeneratedQuestion).where(AIGeneratedQuestion.id == ai_question_id)
            )
            _ai_q = _result.scalar_one_or_none()
            if not _ai_q:
                entry = await _get_ai_similar_cache(ai_question_id)
                if entry:
                    entry["replace"] = {"status": "failed", "error": "题目不存在", "index": index, "difficulty": difficulty}
                    await _set_ai_similar_cache(ai_question_id, entry)
                return

            entry = await _get_ai_similar_cache(ai_question_id)
            if entry and entry.get("replace"):
                entry["replace"]["status"] = "processing"
                await _set_ai_similar_cache(ai_question_id, entry)

            # 从缓存获取已有题目文本以排除重复
            exclude = ""
            if entry and isinstance(entry.get("result"), list):
                exclude = " | ".join(
                    r.get("question_text", "")[:60] for r in entry["result"] if isinstance(r, dict)
                )

            generator = SimilarGenerator()
            sq = await generator.generate_one(
                knowledge_points=[_ai_q.knowledge_point] if _ai_q.knowledge_point else None,
                student_answer=None,
                correct_answer=_ai_q.answer,
                analysis_detail=None,
                question_type=_ai_q.question_type,
                difficulty=difficulty,
                exclude_text=exclude,
            )

            if entry:
                if not sq:
                    entry["replace"] = {"status": "failed", "error": "生成失败，请重试", "index": index, "difficulty": difficulty}
                    await _set_ai_similar_cache(ai_question_id, entry)
                    return
                item = {
                    "question_text": sq.question_text,
                    "answer": sq.answer,
                    "analysis": sq.analysis,
                    "knowledge_point": sq.knowledge_point,
                    "difficulty": sq.difficulty,
                    "question_type": sq.question_type,
                    "options": sq.options,
                    "image_svg": sq.image_svg,
                }
                result_list = entry.get("result")
                if not isinstance(result_list, list):
                    result_list = []
                if 0 <= index < len(result_list):
                    result_list[index] = item
                else:
                    result_list.append(item)  # 下标越界（缓存 TTL 过期等）时追加兜底
                entry["result"] = result_list
                entry["status"] = "completed"
                entry["replace"] = {
                    "status": "completed", "question": item,
                    "index": index, "difficulty": difficulty, "error": None,
                }
                await _set_ai_similar_cache(ai_question_id, entry)
    except Exception as _exc:
        _logger.error("AI single replace failed for q %d: %s", ai_question_id, _exc)
        entry = await _get_ai_similar_cache(ai_question_id)
        if entry:
            entry["replace"] = {"status": "failed", "error": str(_exc), "index": index, "difficulty": difficulty}
            await _set_ai_similar_cache(ai_question_id, entry)


@router.post("/{question_id}/similar-single", status_code=status.HTTP_202_ACCEPTED)
async def generate_ai_similar_single(
    question_id: int,
    data: AIReplaceRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为 AI 题目生成单道同类题（换一题用）——异步任务化。

    原实现为同步等待 LLM（最长 3×120s），前端 axios 120s 必然超时，表现为
    "换一题没反应"。现改为：创建后台任务 + 立即 202 返回，前端轮询
    similar-result 的 replace 字段。
    """
    # body 缺省时用空请求（避免在模块导入期实例化默认值）
    if data is None:
        data = AIReplaceRequest()
    q = await db.get(AIGeneratedQuestion, question_id)
    if not q or q.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="题目不存在")

    # 频率限制检查（LLM 调用成本较高，防止用户快速点击烧穿配额）
    _now = time.time()
    _ts = _ai_similar_single_timestamps[current_user.id]
    _ts[:] = [t for t in _ts if _now - t < _AI_SIMILAR_SINGLE_RATE_WINDOW]
    if len(_ts) >= _AI_SIMILAR_SINGLE_MAX_PER_HOUR:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    _ts.append(_now)

    # 并发守卫：批量生成任务或换题任务进行中时拒绝，避免多个任务同时写缓存
    # 和并发调用 LLM（用户反复点击叠加请求会让模型更慢、更易超时）。
    existing = await _get_ai_similar_cache(question_id)
    if existing:
        if existing["status"] in ("pending", "processing"):
            raise HTTPException(status_code=409, detail="同类题正在生成中，请稍候再试")
        rep = existing.get("replace")
        if rep and rep.get("status") in ("pending", "processing"):
            raise HTTPException(status_code=409, detail="换题正在生成中，请稍候再试")
    else:
        # 缓存缺失（TTL 过期）：重建占位缓存，保证 replace 任务有可写位置
        await _set_ai_similar_cache(question_id, {"status": "completed", "result": []})

    difficulty = data.difficulty if data.difficulty in ("easy", "medium", "hard") else "medium"
    index = data.index if data.index is not None else -1
    entry = await _get_ai_similar_cache(question_id)
    entry["replace"] = {
        "status": "pending", "index": index, "difficulty": difficulty,
        "question": None, "error": None,
    }
    await _set_ai_similar_cache(question_id, entry)

    if _get_settings().DEV_MODE:
        # 用 run_async_in_background 持有任务引用，防止 create_task 裸任务被 GC 回收
        from app.tasks.dev_runner import run_async_in_background
        run_async_in_background(_run_ai_single_replace(question_id, index, difficulty))
    else:
        # 生产模式：投递 Celery 由 worker 执行（状态写 Redis，见 _set_ai_similar_cache）。
        # SET NX 原子占位：堵住并发请求同时通过上方 existing 检查的竞态窗口
        #（与批量生成共用同一把锁，任务启动时释放；锁 key 与状态 key 分离，
        # 避免任务完成后 30 分钟内无法再次触发——见 generate_ai_similar 注释）。
        from app.services.redis_state import redis_state_setnx
        if not await redis_state_setnx(
            f"ai_similar:{question_id}:lock", {"status": "pending"}, ttl=_AI_REPLACE_LOCK_TTL
        ):
            raise HTTPException(status_code=409, detail="换题正在生成中，请稍候再试")
        from app.tasks.analysis_tasks import ai_similar_replace
        ai_similar_replace.delay(question_id, index, difficulty)

    return {"status": "processing", "message": "换题任务已创建"}


@router.put("/{question_id}/content")
async def update_ai_question_content(
    question_id: int,
    data: AIQuestionContentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """编辑 AI 题内容（题干/答案/解析/选项）——收藏页"编辑"弹窗保存入口。

    URL 传收藏锚点 id（独立题即自身 id，大题即组内第一子题 id，前端保证恒有值）：
    - 独立题（group_id 为空）：更新自身 question_text/answer/analysis/options
    - 大题（group_id 非空）：更新 question_context 背景材料 + children 批量更新子题内容
      （含各子题 options）；子题按 id 逐个校验必须属于该大题（group_id 一致），未传 id 的子题保持原样
    仅更新显式传入的字段（Pydantic model_fields_set，"" 为合法清空值），
    绝不触碰 difficulty/knowledge_point/image_svg/作答记录等。
    """
    q = await db.get(AIGeneratedQuestion, question_id)
    if not q or q.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="题目不存在")

    updated_ids: list[int] = []

    if not q.group_id:
        # ── 独立题：只允许更新自身三字段 ──
        if data.children:
            raise HTTPException(status_code=400, detail="该题不是大题，不能批量更新子题")
        if "question_text" in data.model_fields_set:
            q.question_text = data.question_text
        if "answer" in data.model_fields_set:
            q.answer = data.answer
        if "analysis" in data.model_fields_set:
            q.analysis = data.analysis
        if "options" in data.model_fields_set:
            # 选项为 None 时不修改；[] 或具体选项数组时覆盖（[] 落库为 None，与创建接口语义一致）
            q.options = [o.model_dump() for o in data.options] if data.options else None
        updated_ids.append(q.id)
    else:
        # ── 大题：背景材料 + 子题批量更新 ──
        if "question_context" in data.model_fields_set:
            q.question_context = data.question_context
        updated_ids.append(q.id)
        if data.children:
            child_ids = [c.id for c in data.children]
            child_result = await db.execute(
                select(AIGeneratedQuestion).where(AIGeneratedQuestion.id.in_(child_ids))
            )
            children_map = {c.id: c for c in child_result.scalars().all()}
            if len(children_map) != len(child_ids):
                raise HTTPException(status_code=400, detail="存在无效的子题 id")
            for item in data.children:
                child = children_map[item.id]
                if child.group_id != q.group_id:
                    raise HTTPException(status_code=400, detail=f"子题 {item.id} 不属于该大题")
                # 仅更新显式提供的字段（"" 为合法清空值）
                if "question_text" in item.model_fields_set:
                    child.question_text = item.question_text
                if "answer" in item.model_fields_set:
                    child.answer = item.answer
                if "analysis" in item.model_fields_set:
                    child.analysis = item.analysis
                if "options" in item.model_fields_set:
                    # 子题选项同样按"显式传入才覆盖"处理（[] 落库为 None）
                    child.options = [o.model_dump() for o in item.options] if item.options else None
                updated_ids.append(child.id)

    await db.commit()
    # 锚点子题可能同时出现在"背景材料更新"与"children 更新"中，去重保持语义干净
    return {"updated": list(dict.fromkeys(updated_ids)), "message": "内容已更新"}
