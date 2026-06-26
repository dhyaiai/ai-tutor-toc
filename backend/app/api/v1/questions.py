from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.assignment import Assignment
from app.models.question import Question, QuestionStatus, AnalysisTask, AnalysisTaskType, AnalysisTaskStatus
from pydantic import BaseModel
from app.schemas.question import QuestionConfirm, SimilarQuestionsResponse

router = APIRouter(prefix="/questions", tags=["questions"])


class AdjustRegionRequest(BaseModel):
    page_index: int = 0
    x: float
    y: float
    w: float
    h: float
    rotation: int = 0  # 图片旋转角度：0/90/180/270


class ReanalyzeRequest(BaseModel):
    remark: str | None = None


@router.post("/{question_id}/reanalyze", status_code=status.HTTP_202_ACCEPTED)
async def reanalyze_question(
    question_id: int,
    data: ReanalyzeRequest = ReanalyzeRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="题目不存在")

    # Verify ownership
    a_result = await db.execute(
        select(Assignment).where(
            Assignment.id == question.assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    if not a_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="无权访问")

    question.status = QuestionStatus.PENDING
    await db.flush()

    from app.tasks.analysis_tasks import reanalyze_question
    if reanalyze_question is not None:
        reanalyze_question.delay(question_id, data.remark)
    else:
        from app.tasks.analysis_tasks import _do_reanalyze
        import asyncio
        asyncio.create_task(_do_reanalyze(question_id, data.remark))

    return {"task_id": None, "status": "pending", "message": "重新分析任务已创建"}


@router.post("/{question_id}/confirm")
async def confirm_question(
    question_id: int,
    data: QuestionConfirm,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Verify ownership
    a_result = await db.execute(
        select(Assignment).where(
            Assignment.id == question.assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    if not a_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")

    if data.score is not None:
        question.score = data.score
    if data.analysis_detail is not None:
        question.analysis_detail = data.analysis_detail
    question.status = QuestionStatus.CONFIRMED
    await db.flush()

    # 同步更新作业总分
    from app.tasks.analysis_tasks import recalc_assignment_total
    await recalc_assignment_total(question.assignment_id, db, user_id=current_user.id)

    return {
        "question_id": question.id,
        "status": question.status.value,
        "message": "Question confirmed.",
    }


# 同类题生成内存缓存: question_id -> {"status": "pending|processing|completed|failed", "result": [...]}
import asyncio as _asyncio
_similar_cache: dict[int, dict] = {}


async def _run_similar_generation(question_id: int):
    """后台执行同类题生成——逐题生成，每生成完1题就更新缓存"""
    try:
        from sqlalchemy import select as _select
        from app.db.session import async_session_factory
        from app.services.similar_generator import SimilarGenerator

        async with async_session_factory() as _db:
            _result = await _db.execute(_select(Question).where(Question.id == question_id))
            _question = _result.scalar_one_or_none()
            if not _question:
                _similar_cache[question_id] = {"status": "failed", "error": "题目不存在"}
                return

            kps = _question.knowledge_points
            raw_list = kps if isinstance(kps, list) else list(kps.values()) if isinstance(kps, dict) else None
            kp_list = [
                k["name"] if isinstance(k, dict) else str(k)
                for k in raw_list
            ] if raw_list else None

            generator = SimilarGenerator()
            difficulties = ["easy", "medium", "hard"]
            all_results = []

            # 逐题生成，每完成1题就更新缓存；单题超时 240s
            SINGLE_TIMEOUT = 240
            _similar_cache[question_id] = {"status": "processing", "result": all_results}
            for diff in difficulties:
                try:
                    sq = await _asyncio.wait_for(
                        generator.generate_one(
                            knowledge_points=kp_list,
                            student_answer=_question.student_answer,
                            correct_answer=_question.correct_answer,
                            analysis_detail=_question.analysis_detail,
                            question_type=_question.question_type,
                            difficulty=diff,
                            exclude_text=" | ".join(r.get("question_text", "")[:60] for r in all_results),
                        ),
                        timeout=SINGLE_TIMEOUT,
                    )
                except (_asyncio.TimeoutError, Exception) as _gen_exc:
                    if isinstance(_gen_exc, _asyncio.TimeoutError):
                        logger.error("Similar generation timeout for question %d difficulty %s", question_id, diff)
                    else:
                        logger.error("Similar generation failed for question %d difficulty %s: %s", question_id, diff, _gen_exc)
                    sq = None
                if sq:
                    item = {
                        "id": len(all_results),
                        "question_text": sq.question_text,
                        "answer": sq.answer,
                        "knowledge_point": sq.knowledge_point,
                        "difficulty": sq.difficulty,
                        "question_type": sq.question_type,
                        "options": sq.options,
                    }
                    all_results.append(item)
                else:
                    # 单题生成失败，填一个空占位
                    all_results.append({
                        "id": len(all_results),
                        "question_text": "生成失败，请点击换一题",
                        "answer": "",
                        "knowledge_point": kp_list[0] if kp_list else "",
                        "difficulty": diff,
                        "question_type": _question.question_type or "",
                        "options": [],
                    })
                # 每生成1题立即更新缓存，前端轮询即时获取
                _similar_cache[question_id] = {"status": "processing", "result": list(all_results)}

        _similar_cache[question_id] = {"status": "completed", "result": all_results}
    except Exception as _exc:
        _similar_cache[question_id] = {"status": "failed", "error": str(_exc)}


@router.post("/{question_id}/similar", status_code=status.HTTP_202_ACCEPTED)
async def generate_similar(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建同类题生成任务（异步）"""
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Verify ownership
    a_result = await db.execute(
        select(Assignment).where(
            Assignment.id == question.assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    if not a_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")

    # 检查是否已有进行中的任务
    existing = _similar_cache.get(question_id)
    if existing and existing["status"] in ("pending", "processing"):
        return {"status": existing["status"], "message": "已有同类题生成任务进行中"}

    # 标记为 pending 并启动后台任务
    _similar_cache[question_id] = {"status": "pending"}
    _asyncio.create_task(_run_similar_generation(question_id))

    return {"status": "pending", "message": "同类题生成任务已创建"}


@router.post("/{question_id}/similar-single")
async def generate_similar_single(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成单道同类题（换一题用）"""
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    a_result = await db.execute(
        select(Assignment).where(
            Assignment.id == question.assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    if not a_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")

    from app.services.similar_generator import SimilarGenerator

    kps = question.knowledge_points
    raw_list = kps if isinstance(kps, list) else list(kps.values()) if isinstance(kps, dict) else None
    kp_list = [
        k["name"] if isinstance(k, dict) else str(k)
        for k in raw_list
    ] if raw_list else None

    # 从缓存获取已有题目文本以排除重复
    existing = _similar_cache.get(question_id)
    exclude = ""
    if existing and existing.get("result"):
        exclude = " | ".join(
            r.get("question_text", "")[:60] for r in existing["result"] if isinstance(r, dict)
        )

    generator = SimilarGenerator()
    import random
    difficulty = random.choice(["easy", "medium", "hard"])
    sq = await generator.generate_one(
        knowledge_points=kp_list,
        student_answer=question.student_answer,
        correct_answer=question.correct_answer,
        analysis_detail=question.analysis_detail,
        question_type=question.question_type,
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


@router.get("/{question_id}/similar-result")
async def get_similar_result(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取同类题生成结果"""
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Verify ownership
    a_result = await db.execute(
        select(Assignment).where(
            Assignment.id == question.assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    if not a_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")

    cached = _similar_cache.get(question_id)
    if not cached:
        return {"status": "not_found"}

    result_data = cached.get("result", [])
    if cached["status"] == "completed":
        return {"status": "completed", "similar_questions": result_data}
    elif cached["status"] == "failed":
        return {"status": "failed", "error": cached.get("error", "生成失败")}
    else:
        # pending/processing: 返回已生成的部分结果，让前端逐题展示
        return {"status": cached["status"], "similar_questions": result_data}


@router.delete("/{question_id}")
async def delete_question(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除单个题目及其图片"""
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Verify ownership
    a_result = await db.execute(
        select(Assignment).where(
            Assignment.id == question.assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    if not a_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")

    # Delete image file
    from app.services.file_upload import StorageService
    storage = StorageService()
    try:
        await storage.delete_object(question.image_url)
    except Exception:
        pass

    assignment_id = question.assignment_id
    await db.delete(question)
    await db.commit()

    # 同步更新作业总分
    from app.tasks.analysis_tasks import recalc_assignment_total
    await recalc_assignment_total(assignment_id, db, user_id=current_user.id)

    return {"message": "Question deleted", "question_id": question_id}


@router.put("/{question_id}/region")
async def adjust_question_region(
    question_id: int,
    data: AdjustRegionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """调整单个题目的切割区域——从源文件重新切割"""
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Verify ownership
    a_result = await db.execute(
        select(Assignment).where(
            Assignment.id == question.assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    assignment = a_result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=403, detail="Access denied")

    from app.services.file_upload import StorageService
    storage = StorageService()

    # 下载原始文件
    try:
        file_bytes = await storage.get_file_bytes(assignment.file_url)
    except Exception as e:
        raise HTTPException(status_code=500, detail="无法下载源文件")

    import numpy as np
    import cv2

    # 获取指定页面
    page_img = None
    if file_bytes.startswith(b"%PDF"):
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if data.page_index < len(doc):
            page = doc[data.page_index]
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            page_img = img
        doc.close()
    else:
        img_array = np.frombuffer(file_bytes, dtype=np.uint8)
        page_img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

    if page_img is None:
        raise HTTPException(status_code=400, detail="无法加载源页面图片")

    # 使用旋转后裁切（共享函数）
    from app.api.v1.assignments import _rotate_and_cut
    q_img = _rotate_and_cut(page_img, data.rotation, data.x, data.y, data.w, data.h)
    if q_img is None:
        raise HTTPException(status_code=400, detail="无效的切割区域")

    # 删除旧图片
    try:
        await storage.delete_object(question.image_url)
    except Exception:
        pass

    # 切割并保存新图片（旋转后的裁切已在 _rotate_and_cut 中完成）
    _, img_bytes = cv2.imencode(".png", q_img)

    question.image_url = await storage.save_question_image(
        img_bytes.tobytes(), current_user.id, question.assignment_id
    )
    question.page_index = data.page_index
    question.bbox_x = float(x)
    question.bbox_y = float(y)
    question.bbox_w = float(w)
    question.bbox_h = float(h)

    await db.commit()

    return {
        "question_id": question.id,
        "image_url": await storage.get_presigned_url(question.image_url),
        "bbox": {"x": x, "y": y, "w": w, "h": h},
        "message": "Region adjusted.",
    }
