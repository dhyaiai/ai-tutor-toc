import logging
import time
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.assignment import Assignment, AssignmentStatus
from app.models.question import Question, QuestionStatus, AnalysisTask, AnalysisTaskType, AnalysisTaskStatus
from pydantic import BaseModel
from app.schemas.question import SimilarQuestionsResponse

# similar-single 接口频率限制：每用户每小时最多 30 次（LLM 调用成本较高）
_similar_single_timestamps: dict[int, list[float]] = defaultdict(list)
_SIMILAR_SINGLE_MAX_PER_HOUR = 30
_SIMILAR_SINGLE_RATE_WINDOW = 3600

# 生产模式并发占位锁 TTL（锁 key 为 "similar:{question_id}:lock"，与状态 key 分离，
# 避免任务完成后状态仍保留、SET NX 永远失败导致 30 分钟内无法再次生成）：
# 锁在任务启动时释放（redis_state_del），TTL 只兜底覆盖"投递→启动"的窗口，
# 取任务软超时上限即可（锁 TTL 内任务未启动说明队列积压，应当拦截新任务）。
_SIMILAR_LOCK_TTL = 900   # 批量生成任务软超时 900s
_REPLACE_LOCK_TTL = 300   # 单题替换任务软超时 300s

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/questions", tags=["questions"])


def _dispatch_reanalyze(question_id: int, remark: str | None = None):
    """单题重分析分派：dev 模式内联后台执行，生产模式投递 Celery。

    注意：不能像旧的 `if reanalyze_question is not None` 那样判断——
    celery 包安装成功时该对象恒非 None（celery_app.py 无条件构造实例），
    dev 模式（无 Redis）会向死 broker 发布消息导致接口 500（且题目已插入、
    题号已平移，重试会重复插入）。这里与 assignments.py 的 analyze 分派
    （DEV_MODE → dev_runner）保持一致。
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.DEV_MODE:
        from app.tasks.dev_runner import run_async_in_background
        from app.tasks.analysis_tasks import _do_reanalyze
        run_async_in_background(_do_reanalyze(question_id, remark))
    else:
        from app.tasks.analysis_tasks import reanalyze_question
        reanalyze_question.delay(question_id, remark)


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


class QuestionChildContentUpdate(BaseModel):
    """错题大题子题的内容更新项（只允许内容字段，不触碰成绩/状态/图片区域）"""
    id: int  # 子题记录 id
    question_text: str | None = None  # 题干文本（含 $...$ LaTeX）
    correct_answer: str | None = None  # 正确答案
    analysis_detail: str | None = None  # 解析/评分评语


class QuestionContentUpdate(BaseModel):
    """错题内容更新请求：父题字段 + 子题批量更新（均只更新显式传入的字段）"""
    question_text: str | None = None
    correct_answer: str | None = None
    analysis_detail: str | None = None
    children: list[QuestionChildContentUpdate] | None = None  # 大题子题批量更新


@router.post("/{question_id}/reanalyze", status_code=status.HTTP_202_ACCEPTED)
async def reanalyze_question(
    question_id: int,
    data: ReanalyzeRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # body 缺省时用空请求（避免在模块导入期实例化默认值）
    if data is None:
        data = ReanalyzeRequest()
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
    assignment = a_result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(status_code=403, detail="无权访问")

    # 进行中守卫（第一道）：题目级 PROCESSING + 作业级 GRADING/SPLITTING/PROCESSING。
    # 只查题目级不够：整卷分析进行中（assignment.status=GRADING）时批处理按批
    # 把题目置 PROCESSING，排队中的题目仍是 PENDING——若放行会与批处理并发
    # 评分同一道题（BFS 删除/重建子题与批处理写入交错 → 重复子题行、
    # score/answer 后写覆盖先写），因此必须同时拦截作业级分析中状态。
    # （第二道原子抢锁在 analysis_tasks._do_reanalyze_inner 内兜底，
    # 即使两个请求同时通过本检查，也只有第一个任务能抢到锁。）
    if question.status == QuestionStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="该题目正在分析中，请稍候再试")
    if assignment.status in (
        AssignmentStatus.GRADING,
        AssignmentStatus.SPLITTING,
        AssignmentStatus.PROCESSING,
    ):
        raise HTTPException(status_code=409, detail="作业正在分析中，请稍候再试")

    question.status = QuestionStatus.PENDING
    await db.flush()

    _dispatch_reanalyze(question_id, data.remark)

    return {"task_id": None, "status": "pending", "message": "重新分析任务已创建"}


# 同类题生成内存缓存: question_id -> {"status": "pending|processing|completed|failed", "result": [...], "ts": timestamp}
# 使用 TTLCache 限制缓存容量与生命周期，避免内存无限增长。
# 最多缓存 200 个题目，30 分钟未访问自动过期。
import asyncio as _asyncio
from cachetools import TTLCache
_similar_cache: TTLCache = TTLCache(maxsize=200, ttl=1800)


async def _set_similar_cache(question_id: int, value: dict) -> None:
    """写同类题生成任务状态：DEV 写进程内 TTLCache；生产写 Redis（跨 worker 共享）。

    生产多 worker 部署时任务跑在 worker A、轮询打到 worker B，进程内缓存互相
    不可见，必须持久化到 Redis（见 services/redis_state.py）。
    """
    from app.core.config import get_settings
    _similar_cache[question_id] = value
    if not get_settings().DEV_MODE:
        from app.services.redis_state import redis_state_set
        await redis_state_set(f"similar:{question_id}", value)


async def _get_similar_cache(question_id: int) -> dict | None:
    """读同类题生成任务状态（生产模式从 Redis 读，与 _set_similar_cache 对应）。"""
    from app.core.config import get_settings
    if get_settings().DEV_MODE:
        return _similar_cache.get(question_id)
    from app.services.redis_state import redis_state_get
    return await redis_state_get(f"similar:{question_id}")


async def _run_similar_generation(question_id: int):
    """后台执行同类题生成。
    普通题：生成 3 道（easy/medium/hard）逐题更新缓存。
    父题（大题）：只生成 1 道中等难度类似大题，节省 Token。
    """
    try:
        # 任务启动即释放并发占位锁（锁只防"投递→启动"窗口的重复触发；
        # 运行期间由 existing 状态的 pending/processing 拦截新任务）
        from app.core.config import get_settings
        if not get_settings().DEV_MODE:
            from app.services.redis_state import redis_state_del
            await redis_state_del(f"similar:{question_id}:lock")
        from sqlalchemy import select as _select
        from app.db.session import async_session_factory
        from app.services.similar_generator import SimilarGenerator

        async with async_session_factory() as _db:
            _result = await _db.execute(_select(Question).where(Question.id == question_id))
            _question = _result.scalar_one_or_none()
            if not _question:
                await _set_similar_cache(question_id, {"status": "failed", "error": "题目不存在"})
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
                await _set_similar_cache(question_id, {"status": "processing", "result": None, "is_big_question": True})

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
                        # 兜底必须 ≥ 内层最坏耗时（单次 240s × 3 次重试 = 720s），否则误杀重试中的调用
                        timeout=780,
                    )
                except _asyncio.TimeoutError:
                    logger.error("Similar big question generation timeout for question %d", question_id)
                    await _set_similar_cache(question_id, {"status": "failed", "error": "生成超时，请重试", "is_big_question": True})
                    return
                except Exception as _exc:
                    logger.error("Similar big question generation failed for question %d: %s", question_id, _exc)
                    await _set_similar_cache(question_id, {"status": "failed", "error": str(_exc), "is_big_question": True})
                    return

                if not big_q:
                    await _set_similar_cache(question_id, {"status": "failed", "error": "生成失败，请重试", "is_big_question": True})
                    return

                result_data = {
                    "question_context": big_q.question_context,
                    "context_image_svg": big_q.context_image_svg,
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
                            "image_svg": sq.image_svg,
                        }
                        for sq in big_q.sub_questions
                    ],
                }
                await _set_similar_cache(question_id, {"status": "completed", "result": result_data, "is_big_question": True})
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

            # 逐题生成，每完成1题就更新缓存
            # 单题超时兜底必须 ≥ 内层最坏耗时（generate_one 单次 180s × 最多 3 次重试 = 540s），
            # 否则外层 wait_for 会误杀尚未跑完重试的调用
            SINGLE_TIMEOUT = 570
            await _set_similar_cache(question_id, {"status": "processing", "result": all_results, "is_big_question": False})
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
                        "image_svg": sq.image_svg,
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
                await _set_similar_cache(question_id, {"status": "processing", "result": list(all_results), "is_big_question": False})

        await _set_similar_cache(question_id, {"status": "completed", "result": all_results, "is_big_question": False})
    except Exception as _exc:
        await _set_similar_cache(question_id, {"status": "failed", "error": str(_exc)})


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

    # 检查是否已有进行中的任务（生产模式并发防护：SET NX 原子占位，见下）
    existing = await _get_similar_cache(question_id)
    if existing and existing["status"] in ("pending", "processing"):
        return {"status": existing["status"], "message": "已有同类题生成任务进行中"}

    from app.core.config import get_settings
    settings = get_settings()
    if settings.DEV_MODE:
        # 标记为 pending 并启动后台任务（用 dev_runner.run_async_in_background 防止 GC 回收）
        await _set_similar_cache(question_id, {"status": "pending"})
        from app.tasks.dev_runner import run_async_in_background
        run_async_in_background(_run_similar_generation(question_id))
    else:
        # 生产模式：投递 Celery 由 worker 执行（任务内部把状态写 Redis，
        # 多 worker 下轮询接口才能跨进程读到；见 _set_similar_cache）。
        # SET NX 原子占位：并发请求同时通过上方 existing 检查存在竞态窗口，
        # 靠 Redis 原子性兜底，防止同题重复触发双份 LLM 调用。
        # 注意锁 key 与状态 key 分离（锁 = "similar:{id}:lock"，状态 = "similar:{id}"）：
        # 若共用同一 key，任务完成后状态仍保留 completed（TTL 1800s），
        # SET NX 永远失败 → 用户 30 分钟内无法再次生成。任务启动时会删锁。
        from app.services.redis_state import redis_state_setnx
        if not await redis_state_setnx(
            f"similar:{question_id}:lock", {"status": "pending"}, ttl=_SIMILAR_LOCK_TTL
        ):
            return {"status": "pending", "message": "已有同类题生成任务进行中"}
        from app.tasks.analysis_tasks import generate_similar_questions
        generate_similar_questions.delay(question_id)

    return {"status": "pending", "message": "同类题生成任务已创建"}


class SimilarSingleRequest(BaseModel):
    difficulty: str = "medium"  # easy | medium | hard
    index: int = -1  # 普通题要替换的卡片下标；大题/不指定时传 -1


async def _run_single_replace(question_id: int, index: int, difficulty: str):
    """后台执行单题替换（换一题）：生成 1 道同类题，写回缓存 replace 任务结果。

    - 普通题：新题替换缓存 result 中 index 位置的题目
    - 大题（index == -1）：生成 1 道类似大题整体替换 result
    - 失败时不破坏已有结果，只把 replace.status 置为 failed 供前端提示
    """
    try:
        # 任务启动即释放并发占位锁（与批量生成共用同一把锁；见 _SIMILAR_LOCK_TTL 注释）
        from app.core.config import get_settings
        if not get_settings().DEV_MODE:
            from app.services.redis_state import redis_state_del
            await redis_state_del(f"similar:{question_id}:lock")
        from sqlalchemy import select as _select
        from app.db.session import async_session_factory
        from app.services.similar_generator import SimilarGenerator

        async with async_session_factory() as _db:
            _result = await _db.execute(_select(Question).where(Question.id == question_id))
            _question = _result.scalar_one_or_none()
            if not _question:
                entry = await _get_similar_cache(question_id)
                if entry:
                    entry["replace"] = {"status": "failed", "error": "题目不存在", "index": index, "difficulty": difficulty}
                    await _set_similar_cache(question_id, entry)
                return

            children_result = await _db.execute(
                _select(Question).where(Question.parent_id == question_id)
                .order_by(Question.sub_question_index)
            )
            children = children_result.scalars().all()

            generator = SimilarGenerator()
            entry = await _get_similar_cache(question_id)
            if entry and entry.get("replace"):
                entry["replace"]["status"] = "processing"
                await _set_similar_cache(question_id, entry)

            if children:
                # ── 大题：按指定难度生成 1 道类似大题，整体替换 ──
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

                big_q = await generator.generate_similar_big_question(parent_info, children_info, difficulty=difficulty)
                if entry:
                    if not big_q:
                        entry["replace"] = {"status": "failed", "error": "生成失败，请重试", "index": index, "difficulty": difficulty}
                        await _set_similar_cache(question_id, entry)
                        return
                    result_data = {
                        "question_context": big_q.question_context,
                        "context_image_svg": big_q.context_image_svg,
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
                                "image_svg": sq.image_svg,
                            }
                            for sq in big_q.sub_questions
                        ],
                    }
                    entry["result"] = result_data
                    entry["status"] = "completed"
                    entry["replace"] = {
                        "status": "completed", "question": result_data,
                        "index": index, "difficulty": difficulty, "error": None,
                    }
                    await _set_similar_cache(question_id, entry)
                return

            # ── 普通题：按指定难度生成 1 道，替换 index 位置 ──
            kps = _question.knowledge_points
            raw_list = kps if isinstance(kps, list) else list(kps.values()) if isinstance(kps, dict) else None
            kp_list = [
                k["name"] if isinstance(k, dict) else str(k)
                for k in raw_list
            ] if raw_list else None

            # 从缓存获取已有题目文本以排除重复
            exclude = ""
            if entry and isinstance(entry.get("result"), list):
                exclude = " | ".join(
                    r.get("question_text", "")[:60] for r in entry["result"] if isinstance(r, dict)
                )

            sq = await generator.generate_one(
                knowledge_points=kp_list,
                student_answer=_question.student_answer,
                correct_answer=_question.correct_answer,
                analysis_detail=_question.analysis_detail,
                question_type=_question.question_type,
                difficulty=difficulty,
                exclude_text=exclude,
            )

            if entry:
                if not sq:
                    entry["replace"] = {"status": "failed", "error": "生成失败，请重试", "index": index, "difficulty": difficulty}
                    await _set_similar_cache(question_id, entry)
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
                await _set_similar_cache(question_id, entry)
    except Exception as _exc:
        logger.error("Single replace failed for question %d: %s", question_id, _exc)
        entry = await _get_similar_cache(question_id)
        if entry:
            entry["replace"] = {"status": "failed", "error": str(_exc), "index": index, "difficulty": difficulty}
            await _set_similar_cache(question_id, entry)


@router.post("/{question_id}/similar-single", status_code=status.HTTP_202_ACCEPTED)
async def generate_similar_single(
    question_id: int,
    data: SimilarSingleRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成单道同类题（换一题用）——异步任务化。

    原实现为同步等待 LLM（generate_one 最多 3 次 × 120s = 360s），而前端
    axios 超时仅 120s，必然前端超时失败，表现为"换一题没反应"。现改为：
    创建后台任务 + 立即 202 返回，前端轮询 similar-result 的 replace 字段。
    """
    # body 缺省时用空请求（避免在模块导入期实例化默认值）
    if data is None:
        data = SimilarSingleRequest()
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

    # 频率限制检查（LLM 调用成本较高，防止用户快速点击烧穿配额）
    _now = time.time()
    _ts = _similar_single_timestamps[current_user.id]
    _ts[:] = [t for t in _ts if _now - t < _SIMILAR_SINGLE_RATE_WINDOW]
    if len(_ts) >= _SIMILAR_SINGLE_MAX_PER_HOUR:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    _ts.append(_now)

    # 并发守卫：批量生成任务或换题任务进行中时拒绝，避免多个任务同时写缓存
    # 和并发调用 LLM（用户反复点击叠加请求会让模型更慢、更易超时）。
    existing = await _get_similar_cache(question_id)
    if existing:
        if existing["status"] in ("pending", "processing"):
            raise HTTPException(status_code=409, detail="同类题正在生成中，请稍候再试")
        rep = existing.get("replace")
        if rep and rep.get("status") in ("pending", "processing"):
            raise HTTPException(status_code=409, detail="换题正在生成中，请稍候再试")
    else:
        # 缓存缺失（TTL 过期）：重建占位缓存，保证 replace 任务有可写位置
        await _set_similar_cache(question_id, {"status": "completed", "result": [], "is_big_question": False})

    difficulty = data.difficulty if data.difficulty in ("easy", "medium", "hard") else "medium"
    index = data.index if data.index is not None else -1
    entry = await _get_similar_cache(question_id)
    entry["replace"] = {
        "status": "pending", "index": index, "difficulty": difficulty,
        "question": None, "error": None,
    }
    await _set_similar_cache(question_id, entry)

    from app.core.config import get_settings
    if get_settings().DEV_MODE:
        # 用 run_async_in_background 持有任务引用，防止 create_task 裸任务被 GC 回收
        from app.tasks.dev_runner import run_async_in_background
        run_async_in_background(_run_single_replace(question_id, index, difficulty))
    else:
        # 生产模式：投递 Celery 由 worker 执行（状态写 Redis，见 _set_similar_cache）。
        # SET NX 原子占位：堵住并发请求同时通过上方 existing 检查的竞态窗口
        #（与批量生成共用同一把锁，任务启动时释放；锁 key 与状态 key 分离，
        # 避免任务完成后 30 分钟内无法再次触发——见 generate_similar 注释）。
        from app.services.redis_state import redis_state_setnx
        if not await redis_state_setnx(
            f"similar:{question_id}:lock", {"status": "pending"}, ttl=_REPLACE_LOCK_TTL
        ):
            raise HTTPException(status_code=409, detail="换题正在生成中，请稍候再试")
        from app.tasks.analysis_tasks import similar_replace
        similar_replace.delay(question_id, index, difficulty)

    return {"status": "processing", "message": "换题任务已创建"}


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

    cached = await _get_similar_cache(question_id)
    if not cached:
        return {"status": "not_found"}

    is_big = cached.get("is_big_question", False)
    result_data = cached.get("result", [])
    status = cached["status"]
    # 换一题任务状态（pending/processing/completed/failed），前端轮询消费
    replace = cached.get("replace")

    if is_big:
        # 大题返回单个对象而非数组
        if status == "completed":
            return {"status": "completed", "similar_questions": result_data, "is_big_question": True, "replace": replace}
        elif status == "failed":
            return {"status": "failed", "error": cached.get("error", "生成失败"), "is_big_question": True, "replace": replace}
        else:
            return {"status": status, "similar_questions": None, "is_big_question": True, "replace": replace}

    if status == "completed":
        return {"status": "completed", "similar_questions": result_data, "replace": replace}
    elif status == "failed":
        return {"status": "failed", "error": cached.get("error", "生成失败"), "replace": replace}
    else:
        # pending/processing: 返回已生成的部分结果，让前端逐题展示
        return {"status": cached["status"], "similar_questions": result_data, "replace": replace}


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
    deleted_number = question.question_number
    is_top_level = question.parent_id is None
    await db.delete(question)
    await db.flush()

    # 删除顶层题后，后续题号自动前移，避免跳号（子题的 question_number 跟随父题同步前移）
    if is_top_level:
        shift_result = await db.execute(
            select(Question).where(
                Question.assignment_id == assignment_id,
                Question.question_number > deleted_number,
            )
        )
        for q in shift_result.scalars().all():
            q.question_number -= 1

    await db.commit()

    # 同步更新作业总分
    from app.tasks.analysis_tasks import recalc_assignment_total
    await recalc_assignment_total(assignment_id, db, user_id=current_user.id)

    return {"message": "Question deleted", "question_id": question_id}


@router.post("/{question_id}/insert-below", status_code=status.HTTP_201_CREATED)
async def insert_question_below(
    question_id: int,
    data: AdjustRegionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """在指定题目下方插入一道新题（补切漏切题目）——
    从源文件按用户框选区域切图，后续题目题号自动 +1，并触发新题 AI 分析"""
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    if question.parent_id is not None:
        raise HTTPException(status_code=400, detail="只能在顶层题目下方插入新题")

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
    except Exception:
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
        import asyncio
        from app.utils.pdf_renderer_utils import _render_pdf_pages_bgr
        # fitz 栅格化是同步 CPU 重活，线程池执行避免阻塞事件循环；
        # 只渲染涉及的区域页（不存在的页在 enumerate 中自然跳过）
        page_images = await asyncio.to_thread(
            _render_pdf_pages_bgr, file_bytes, page_indices=needed_pages
        )
    else:
        img_array = np.frombuffer(file_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is not None:
            page_images[0] = img

    # 逐区域旋转后裁切（共享函数），多区域垂直拼接
    from app.utils.pdf_renderer_utils import _rotate_and_cut, _merge_images
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
    _, img_bytes = cv2.imencode(".png", q_img)

    image_url = await storage.save_question_image(
        img_bytes.tobytes(), current_user.id, question.assignment_id
    )

    # 后续题目题号统一 +1，为新题腾出位置（子题的 question_number 跟随父题同步后移）
    shift_result = await db.execute(
        select(Question).where(
            Question.assignment_id == question.assignment_id,
            Question.question_number > question.question_number,
        )
    )
    for q in shift_result.scalars().all():
        q.question_number += 1

    new_question = Question(
        assignment_id=question.assignment_id,
        question_number=question.question_number + 1,
        image_url=image_url,
        status=QuestionStatus.PENDING,
        page_index=data.page_index,
        bbox_x=float(data.x),
        bbox_y=float(data.y),
        bbox_w=float(data.w),
        bbox_h=float(data.h),
    )
    db.add(new_question)
    await db.commit()
    await db.refresh(new_question)

    # 触发新题 AI 分析（复用单题重分析链路）
    _dispatch_reanalyze(new_question.id, None)

    return {
        "question_id": new_question.id,
        "question_number": new_question.question_number,
        "message": "Question inserted.",
    }


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
        import asyncio
        from app.utils.pdf_renderer_utils import _render_pdf_pages_bgr
        # fitz 栅格化是同步 CPU 重活，线程池执行避免阻塞事件循环；
        # 只渲染涉及的区域页（不存在的页在 enumerate 中自然跳过）
        page_images = await asyncio.to_thread(
            _render_pdf_pages_bgr, file_bytes, page_indices=needed_pages
        )
    else:
        img_array = np.frombuffer(file_bytes, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is not None:
            page_images[0] = img

    # 逐区域旋转后裁切（共享函数），多区域垂直拼接
    from app.utils.pdf_renderer_utils import _rotate_and_cut, _merge_images
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


@router.put("/{question_id}/content")
async def update_question_content(
    question_id: int,
    data: QuestionContentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """编辑题目内容（题干/答案/解析）——收藏页"编辑"弹窗保存入口。

    仅更新显式传入的内容字段（Pydantic model_fields_set 判断，"" 为合法清空值），
    绝不触碰 score/full_score/status/student_answer/image_url/bbox 等非内容字段。
    大题支持 children 批量更新子题内容；未传 id 的子题保持原样。
    """
    result = await db.execute(select(Question).where(Question.id == question_id))
    question = result.scalar_one_or_none()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Verify ownership（与 DELETE /{question_id} 一致的归属校验）
    a_result = await db.execute(
        select(Assignment).where(
            Assignment.id == question.assignment_id,
            Assignment.creator_id == current_user.id,
        )
    )
    if not a_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")

    updated_ids: list[int] = []

    # 父题自身三字段：仅更新显式提供的字段（model_fields_set 区分"未传"与"显式清空"）
    if "question_text" in data.model_fields_set:
        question.question_text = data.question_text
    if "correct_answer" in data.model_fields_set:
        question.correct_answer = data.correct_answer
    if "analysis_detail" in data.model_fields_set:
        question.analysis_detail = data.analysis_detail
    updated_ids.append(question.id)

    # 大题子题批量更新：校验该题确为顶层大题，且每个子题都属于该父题
    if data.children:
        if question.parent_id is not None:
            raise HTTPException(status_code=400, detail="该题不是顶层大题，不能批量更新子题")
        child_ids = [c.id for c in data.children]
        child_result = await db.execute(
            select(Question).where(Question.id.in_(child_ids))
        )
        children_map = {c.id: c for c in child_result.scalars().all()}
        if len(children_map) != len(child_ids):
            raise HTTPException(status_code=400, detail="存在无效的子题 id")
        for item in data.children:
            child = children_map[item.id]
            if child.parent_id != question_id:
                raise HTTPException(status_code=400, detail=f"子题 {item.id} 不属于该大题")
            # 仅更新显式提供的字段（"" 为合法清空值）
            if "question_text" in item.model_fields_set:
                child.question_text = item.question_text
            if "correct_answer" in item.model_fields_set:
                child.correct_answer = item.correct_answer
            if "analysis_detail" in item.model_fields_set:
                child.analysis_detail = item.analysis_detail
            updated_ids.append(child.id)

    await db.commit()
    return {"updated": updated_ids, "message": "内容已更新"}
