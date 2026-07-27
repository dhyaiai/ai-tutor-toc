from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.assignment import Assignment
from app.models.question import Question, QuestionStatus, AnalysisTask, AnalysisTaskType, AnalysisTaskStatus
from pydantic import BaseModel
from app.schemas.question import SimilarQuestionsResponse

router = APIRouter(prefix="/questions", tags=["questions"])


class AdjustRegionItem(BaseModel):
    """单个裁切区域（额外区域使用，如 A4 双栏左右分栏）"""
    page_index: int = 0
    x: float
    y: float
    w: float
    h: float
    rotation: int = 0  # 图片旋转角度：0/90/180/270


class AdjustRegionRequest(BaseModel):
    page_index: int = 0
    x: float
    y: float
    w: float
    h: float
    rotation: int = 0  # 图片旋转角度：0/90/180/270
    # 同题额外区域（与主区域垂直拼接，支持双栏/跨页）
    extra_regions: list[AdjustRegionItem] = []


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


# 同类题生成内存缓存: question_id -> {"status": "pending|processing|completed|failed", "result": [...]}
import asyncio as _asyncio
_similar_cache: dict[int, dict] = {}


async def _run_similar_generation(question_id: int):
    """后台执行同类题生成。
    普通题：生成 3 道（easy/medium/hard）逐题更新缓存。
    父题（大题）：只生成 1 道中等难度类似大题，节省 Token。
    """
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

            generator = SimilarGenerator()

            # ── 检测是否为父题（大题）──
            children_result = await _db.execute(
                _select(Question).where(Question.parent_id == question_id)
                .order_by(Question.sub_question_index)
            )
            children = children_result.scalars().all()

            if children:
                # ── 大题：生成 1 道类似大题 ──
                _similar_cache[question_id] = {"status": "processing", "result": None, "is_big_question": True}

                # 构建父题和子题信息字典
                parent_info = {
                    "question_number": _question.question_number,
                    "question_type": _question.question_type,
                    "knowledge_points": _question.knowledge_points,
                }
                children_info = []
                for child in children:
                    children_info.append({
                        "question_type": child.question_type,
                        "student_answer": child.student_answer,
                        "correct_answer": child.correct_answer,
                        "knowledge_points": child.knowledge_points,
                        "analysis_detail": child.analysis_detail,
                        "score": child.score,
                        "full_score": child.full_score,
                    })

                try:
                    big_q = await _asyncio.wait_for(
                        generator.generate_similar_big_question(parent_info, children_info, difficulty="medium"),
                        timeout=300,
                    )
                except _asyncio.TimeoutError:
                    logger.error("Similar big question generation timeout for question %d", question_id)
                    _similar_cache[question_id] = {"status": "failed", "error": "生成超时，请重试", "is_big_question": True}
                    return
                except Exception as _exc:
                    logger.error("Similar big question generation failed for question %d: %s", question_id, _exc)
                    _similar_cache[question_id] = {"status": "failed", "error": str(_exc), "is_big_question": True}
                    return

                if not big_q:
                    _similar_cache[question_id] = {"status": "failed", "error": "生成失败，请重试", "is_big_question": True}
                    return

                result_data = {
                    "question_context": big_q.question_context,
                    "sub_questions": [
                        {
                            "question_text": sq.question_text,
                            "answer": sq.answer,
                            "analysis": sq.analysis,
                            "knowledge_point": sq.knowledge_point,
                            "difficulty": sq.difficulty,
                            "question_type": sq.question_type,
                            "options": sq.options,
                            "full_score": sq.full_score,
                        }
                        for sq in big_q.sub_questions
                    ],
                }
                _similar_cache[question_id] = {"status": "completed", "result": result_data, "is_big_question": True}
                return

            # ── 普通题：逐题生成 3 道（easy/medium/hard）──
            kps = _question.knowledge_points
            raw_list = kps if isinstance(kps, list) else list(kps.values()) if isinstance(kps, dict) else None
            kp_list = [
                k["name"] if isinstance(k, dict) else str(k)
                for k in raw_list
            ] if raw_list else None

            difficulties = ["easy", "medium", "hard"]
            all_results = []

            # 逐题生成，每完成1题就更新缓存；单题超时 240s
            SINGLE_TIMEOUT = 240
            _similar_cache[question_id] = {"status": "processing", "result": all_results, "is_big_question": False}
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
                        "analysis": sq.analysis,
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
                        "analysis": "",
                        "knowledge_point": kp_list[0] if kp_list else "",
                        "difficulty": diff,
                        "question_type": _question.question_type or "",
                        "options": [],
                    })
                # 每生成1题立即更新缓存，前端轮询即时获取
                _similar_cache[question_id] = {"status": "processing", "result": list(all_results), "is_big_question": False}

        _similar_cache[question_id] = {"status": "completed", "result": all_results, "is_big_question": False}
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


class SimilarSingleRequest(BaseModel):
    difficulty: str = "medium"  # easy | medium | hard


@router.post("/{question_id}/similar-single")
async def generate_similar_single(
    question_id: int,
    data: SimilarSingleRequest = SimilarSingleRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成单道同类题（换一题用）。
    普通题：生成 1 道指定难度同类题。
    父题（大题）：生成 1 道指定难度类似大题替换当前。"""
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

    # ── 检测是否为父题（大题）──
    children_result = await db.execute(
        select(Question).where(Question.parent_id == question_id)
        .order_by(Question.sub_question_index)
    )
    children = children_result.scalars().all()

    generator = SimilarGenerator()
    difficulty = data.difficulty if data.difficulty in ("easy", "medium", "hard") else "medium"

    if children:
        # ── 大题：按指定难度生成类似大题 ──
        parent_info = {
            "question_number": question.question_number,
            "question_type": question.question_type,
            "knowledge_points": question.knowledge_points,
        }
        children_info = []
        for child in children:
            children_info.append({
                "question_type": child.question_type,
                "student_answer": child.student_answer,
                "correct_answer": child.correct_answer,
                "knowledge_points": child.knowledge_points,
                "analysis_detail": child.analysis_detail,
                "score": child.score,
                "full_score": child.full_score,
            })

        big_q = await generator.generate_similar_big_question(parent_info, children_info, difficulty=difficulty)
        if not big_q:
            raise HTTPException(status_code=500, detail="生成失败，请稍后重试")

        return {
            "is_big_question": True,
            "question_context": big_q.question_context,
            "sub_questions": [
                {
                    "question_text": sq.question_text,
                    "answer": sq.answer,
                    "analysis": sq.analysis,
                    "knowledge_point": sq.knowledge_point,
                    "difficulty": sq.difficulty,
                    "question_type": sq.question_type,
                    "options": sq.options,
                    "full_score": sq.full_score,
                }
                for sq in big_q.sub_questions
            ],
        }

    # ── 普通题：随机难度单题生成 ──
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
        if isinstance(existing["result"], list):
            exclude = " | ".join(
                r.get("question_text", "")[:60] for r in existing["result"] if isinstance(r, dict)
            )

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
        "analysis": sq.analysis,
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

    is_big = cached.get("is_big_question", False)
    result_data = cached.get("result", [])
    status = cached["status"]

    if is_big:
        # 大题返回单个对象而非数组
        if status == "completed":
            return {"status": "completed", "similar_questions": result_data, "is_big_question": True}
        elif status == "failed":
            return {"status": "failed", "error": cached.get("error", "生成失败"), "is_big_question": True}
        else:
            return {"status": status, "similar_questions": None, "is_big_question": True}

    if status == "completed":
        return {"status": "completed", "similar_questions": result_data}
    elif status == "failed":
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

    # 主区域 + 额外区域（双栏/跨页），按需渲染涉及的页面
    all_regions: list = [
        AdjustRegionItem(
            page_index=data.page_index, x=data.x, y=data.y,
            w=data.w, h=data.h, rotation=data.rotation,
        )
    ] + list(data.extra_regions)
    needed_pages = {r.page_index for r in all_regions}

    page_images: dict[int, np.ndarray] = {}
    if file_bytes.startswith(b"%PDF"):
        import fitz
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page_idx in needed_pages:
            if page_idx >= len(doc):
                continue
            page = doc[page_idx]
            mat = fitz.Matrix(2.0, 2.0)
            pix = page.get_pixmap(matrix=mat)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if img.shape[2] == 4:
                img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            elif img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
            else:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            page_images[page_idx] = img
        doc.close()
    else:
        img_array = np.frombuffer(file_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is not None:
            page_images[0] = img

    # 逐区域旋转后裁切（共享函数），多区域垂直拼接
    from app.api.v1.assignments import _rotate_and_cut, _merge_images
    cut_images: list[np.ndarray] = []
    for region in all_regions:
        page_img = page_images.get(region.page_index)
        if page_img is None:
            continue
        cut = _rotate_and_cut(page_img, region.rotation, region.x, region.y, region.w, region.h)
        if cut is not None:
            cut_images.append(cut)

    if not cut_images:
        raise HTTPException(status_code=400, detail="无效的切割区域")

    q_img = cut_images[0] if len(cut_images) == 1 else _merge_images(cut_images)

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
    # bbox 记录主区域坐标（下次调整时预填主区域）
    question.page_index = data.page_index
    question.bbox_x = float(data.x)
    question.bbox_y = float(data.y)
    question.bbox_w = float(data.w)
    question.bbox_h = float(data.h)

    await db.commit()

    return {
        "question_id": question.id,
        "image_url": await storage.get_presigned_url(question.image_url),
        "bbox": {"x": data.x, "y": data.y, "w": data.w, "h": data.h},
        "message": "Region adjusted.",
    }
