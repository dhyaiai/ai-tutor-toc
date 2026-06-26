"""AI 生成题目 API——保存、列表、作答提交"""

import json
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.ai_question import AIGeneratedQuestion, AIQuestionAnswer
from app.schemas.ai_question import (
    SaveAIQuestionRequest, AIQuestionResponse, AnswerItem,
    OptionItem, SubmitAnswerResponse,
)

router = APIRouter(prefix="/ai-questions", tags=["ai-questions"])


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
        question_type=body.question_type,
        knowledge_point=body.knowledge_point,
        difficulty=body.difficulty,
        options=[o.model_dump() for o in body.options] if body.options else None,
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
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出AI生成题目（繁星驱动板块）"""
    conditions = [AIGeneratedQuestion.user_id == current_user.id]

    # 难度直接过滤
    if difficulty:
        conditions.append(AIGeneratedQuestion.difficulty == difficulty)

    # 通过关联查询原题来过滤年级/科目/学期
    if grade or subject or semester or question_type:
        from app.models.question import Question
        from app.models.assignment import Assignment
        subq = select(AIGeneratedQuestion.id)
        if grade or subject or semester:
            subq = subq.join(Question, AIGeneratedQuestion.source_question_id == Question.id) \
                .join(Assignment, Question.assignment_id == Assignment.id)
            if grade:
                subq = subq.where(Assignment.grade == grade)
            if subject:
                subq = subq.where(Assignment.subject == subject)
            if semester:
                subq = subq.where(Assignment.semester == semester)
        if question_type:
            if question_type == "未知":
                subq = subq.where(
                    (AIGeneratedQuestion.question_type == None) |
                    (AIGeneratedQuestion.question_type == "")
                )
            else:
                subq = subq.where(AIGeneratedQuestion.question_type == question_type)
        conditions.append(AIGeneratedQuestion.id.in_(subq))

    count_q = select(func.count()).select_from(AIGeneratedQuestion).where(*conditions)
    total = (await db.execute(count_q)).scalar() or 0

    list_q = (
        select(AIGeneratedQuestion)
        .where(*conditions)
        .order_by(desc(AIGeneratedQuestion.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(list_q)).scalars().all()

    items = []
    for q in rows:
        ans_q = select(AIQuestionAnswer).where(
            AIQuestionAnswer.question_id == q.id,
            AIQuestionAnswer.user_id == current_user.id,
        ).order_by(AIQuestionAnswer.answered_at)
        ans_rows = (await db.execute(ans_q)).scalars().all()

        items.append(AIQuestionResponse(
            id=q.id,
            source_question_id=q.source_question_id,
            question_text=q.question_text,
            answer=q.answer,
            question_type=q.question_type,
            knowledge_point=q.knowledge_point,
            difficulty=q.difficulty,
            options=[OptionItem(**o) for o in q.options] if q.options else None,
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
        ))

    return {"items": items, "total": total}


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
        question_type=q.question_type,
        knowledge_point=q.knowledge_point,
        difficulty=q.difficulty,
        options=[OptionItem(**o) for o in q.options] if q.options else None,
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
    question_type: str = Form(""),
    knowledge_point: str = Form(""),
    difficulty: str = Form("medium"),
    options_json: str = Form(""),
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
        question_type=question_type or None,
        knowledge_point=knowledge_point or None,
        difficulty=difficulty or "medium",
        options=options_list,
    )
    db.add(q)
    await db.flush()

    # 处理图片上传
    selected = json.loads(selected_options) if selected_options else None
    image_url = None
    if answer_image:
        from app.services.file_upload import StorageService
        storage = StorageService()
        content = await answer_image.read()
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

    # 选择题：直接判对错
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
        generator = SimilarGenerator()
        user_content = answer_text or ""
        if image_url:
            user_content += " [上传了答案图片]"
        grading = await generator.grade_answer(
            question_text=question_text,
            correct_answer=answer,
            user_answer=user_content,
            knowledge_point=knowledge_point or "",
            full_score=float(source_full_score),
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

    options_list = json.loads(selected_options) if selected_options else None
    image_url = None

    if answer_image:
        from app.services.file_upload import StorageService
        storage = StorageService()
        content = await answer_image.read()
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

    # 选择题：直接判断对错
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
        generator = SimilarGenerator()
        user_content = answer_text or ""
        if image_url:
            user_content += f" [上传了答案图片]"
        result = await generator.grade_answer(
            question_text=q.question_text,
            correct_answer=q.answer,
            user_answer=user_content,
            knowledge_point=q.knowledge_point or "",
            full_score=float(source_full_score),
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

_ai_similar_cache: dict[int, dict] = {}


async def _run_ai_similar_generation(ai_question_id: int):
    """后台执行 AI 题目的同类题生成——逐题生成，每完成 1 题就更新缓存"""
    import logging
    _logger = logging.getLogger(__name__)
    try:
        from sqlalchemy import select as _select
        from app.db.session import async_session_factory
        from app.services.similar_generator import SimilarGenerator

        async with async_session_factory() as _db:
            _result = await _db.execute(
                _select(AIGeneratedQuestion).where(AIGeneratedQuestion.id == ai_question_id)
            )
            _ai_q = _result.scalar_one_or_none()
            if not _ai_q:
                _ai_similar_cache[ai_question_id] = {"status": "failed", "error": "题目不存在"}
                return

            generator = SimilarGenerator()
            difficulties = ["easy", "medium", "hard"]
            all_results = []
            SINGLE_TIMEOUT = 90

            _ai_similar_cache[ai_question_id] = {"status": "processing", "result": all_results}
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
                        "knowledge_point": sq.knowledge_point,
                        "difficulty": sq.difficulty,
                        "question_type": sq.question_type,
                        "options": sq.options,
                    })
                else:
                    all_results.append({
                        "id": len(all_results),
                        "question_text": "生成失败，请点击换一题",
                        "answer": "",
                        "knowledge_point": _ai_q.knowledge_point or "",
                        "difficulty": diff,
                        "question_type": _ai_q.question_type or "",
                        "options": [],
                    })
                _ai_similar_cache[ai_question_id] = {"status": "processing", "result": list(all_results)}

        _ai_similar_cache[ai_question_id] = {"status": "completed", "result": all_results}
    except Exception as _exc:
        _ai_similar_cache[ai_question_id] = {"status": "failed", "error": str(_exc)}


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

    existing = _ai_similar_cache.get(question_id)
    if existing and existing["status"] in ("pending", "processing"):
        return {"status": existing["status"], "message": "已有同类题生成任务进行中"}

    _ai_similar_cache[question_id] = {"status": "pending"}
    _ai_asyncio.create_task(_run_ai_similar_generation(question_id))
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

    cached = _ai_similar_cache.get(question_id)
    if not cached:
        return {"status": "not_found"}

    result_data = cached.get("result", [])
    if cached["status"] == "completed":
        return {"status": "completed", "similar_questions": result_data}
    elif cached["status"] == "failed":
        return {"status": "failed", "error": cached.get("error", "生成失败")}
    else:
        return {"status": cached["status"], "similar_questions": result_data}


@router.post("/{question_id}/similar-single")
async def generate_ai_similar_single(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为 AI 题目生成单道同类题（换一题用）"""
    q = await db.get(AIGeneratedQuestion, question_id)
    if not q or q.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="题目不存在")

    from app.services.similar_generator import SimilarGenerator
    import random

    existing = _ai_similar_cache.get(question_id)
    exclude = ""
    if existing and existing.get("result"):
        exclude = " | ".join(
            r.get("question_text", "")[:60] for r in existing["result"] if isinstance(r, dict)
        )

    generator = SimilarGenerator()
    difficulty = random.choice(["easy", "medium", "hard"])
    sq = await generator.generate_one(
        knowledge_points=[q.knowledge_point] if q.knowledge_point else None,
        student_answer=None,
        correct_answer=q.answer,
        analysis_detail=None,
        question_type=q.question_type,
        difficulty=difficulty,
        exclude_text=exclude,
    )

    if not sq:
        raise HTTPException(status_code=500, detail="生成失败，请稍后重试")

    return {
        "question_text": sq.question_text,
        "answer": sq.answer,
        "knowledge_point": sq.knowledge_point,
        "difficulty": sq.difficulty,
        "question_type": sq.question_type,
        "options": sq.options,
    }
