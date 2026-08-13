"""
AI 助教对话 API（SSE 流式）

支持可选的 session_id 参数，传入后会在对话完成后自动保存消息到对应会话，
实现会话的持久化存储。
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db
from app.core.security import get_current_user
from app.db.session import async_session_factory
from app.models.user import User
from app.models.conversation import Conversation, ConversationMessage
from app.schemas.ai import ChatRequest, ExplainCheckRequest, ExplainRequest, FeedbackRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-tutor", tags=["ai-tutor"])

# /chat 接口并发限制：每用户同时最多 2 个在途 SSE 对话，防止恶意并发消耗 LLM 配额
# key=user_id, value=当前活跃请求数
# TODO(#security): 多 worker 部署前迁移到 redis，key=chat_concurrent:{user_id}, TTL=3600
_chat_concurrent: dict[int, int] = defaultdict(int)
_CHAT_MAX_CONCURRENT_PER_USER = 2

# /chat 接口调用频率限制：每用户每小时最多 60 次
# key=user_id, value=[调用时间戳列表]
# TODO(#security): 多 worker 部署前迁移到 redis ZSET，key=chat_rate:{user_id}, TTL=3600
_chat_rate_timestamps: dict[int, list[float]] = defaultdict(list)
_CHAT_MAX_PER_HOUR = 60
_CHAT_RATE_WINDOW_SECONDS = 3600

# 后台任务引用保护集合：防止 create_task 创建的清理任务被 GC 回收
_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def _stream_chat(
    message: str,
    history: list[dict],
    context: dict | None,
    db,
    user_id: int,
    session_id: int | None = None,
):
    """
    SSE 流式返回 Agent 对话

    参数：
    - message: 用户输入的消息
    - history: 对话历史
    - context: 年级/学科等上下文
    - session_id: 可选的会话ID，传入后自动保存消息

    生成的事件类型：
    - reasoning: AI的思考过程
    - tool_call: AI调用工具
    - tool_result: 工具执行结果
    - token: AI回复的文本片段（流式）
    - error: 错误信息
    - done: 对话结束
    """
    import asyncio

    from app.services.agent.agent_executor import AgentExecutor

    executor = AgentExecutor(db, user_id)

    # 收集完整的AI回复内容，用于后续持久化
    full_content = ""
    full_reasoning = ""
    tool_calls_list: list[str] = []
    # 用户消息是否已保存、assistant 消息是否已保存（done 事件）
    saved_user_msg_id: int | None = None
    saved_assistant = False

    # 请求开始时立即保存用户消息并提交。
    # 生成订正本/报告等耗时工具调用期间前端可能超时、用户可能刷新/切会话，
    # 若等到 done 才一次性保存，流一旦中断整轮消息（含用户提问）都会丢失。
    if session_id:
        saved_user_msg_id = await _save_user_message(
            db=db, user_id=user_id, session_id=session_id, message=message
        )

    # 并发计数必须随生成器生命周期增减，而非端点函数：
    # 端点 return StreamingResponse 的瞬间生成器尚未被迭代，若在端点 finally 里递减，
    # 计数立刻归零，"每用户最多 2 个在途对话"的并发上限会完全失效。
    # 频率计数同理放这里：流真正开始才 append，流创建后立即失败（LLM 500 等）不计费。
    _chat_concurrent[user_id] += 1
    _chat_rate_timestamps[user_id].append(time.time())

    try:
        async for event in executor.run(
            message=message,
            history=history,
            context=context,
        ):
            # 收集回复内容用于持久化
            if event.get("type") == "token":
                full_content += event.get("content", "")
            elif event.get("type") == "reasoning":
                full_reasoning += event.get("content", "")
            elif event.get("type") == "tool_call":
                tool_calls_list.append(event.get("name", ""))
            elif event.get("type") == "done" and session_id:
                # 内容全空（error+done 且无任何输出）时不保存 assistant，
                # saved_assistant 保持 False → finally 兜底清理删除孤立的用户提问，
                # 避免下轮对话把"有问无答"的消息当 history 重发给 LLM（C4）。
                # 非空时保存 AI 回复（用户消息已在请求开始时保存）。
                if full_content.strip() or full_reasoning or tool_calls_list:
                    # saved_assistant 必须等保存成功后置位：_save_assistant_message
                    # 内部吞掉一切异常，若提前置位，保存失败时 finally 的
                    # not saved_assistant 兜底清理会失效，会话残留"有问无答"
                    saved_assistant = await _save_assistant_message(
                        db=db,
                        user_id=user_id,
                        session_id=session_id,
                        assistant_content=full_content,
                        reasoning=full_reasoning,
                        tool_calls=tool_calls_list,
                    )

            yield f"event: message\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
    finally:
        # 释放并发占位（与生成器生命周期绑定）
        _chat_concurrent[user_id] -= 1

        # 客户端断开/异常中断时生成器被取消，done 事件不会产出，
        # assistant 消息未保存 → 会话里只剩孤立的用户提问。
        # 兜底清理：有部分回复则保存"中断"占位消息，完全没回复则删除用户消息。
        # 注意：被取消的生成器内不能直接 await（会立即抛 CancelledError），
        # 用 shield 把清理任务脱离取消作用域，事件循环会继续执行完它。
        # 引用保护：shield 内的 task 必须保存引用，否则可能被 GC 回收。
        if session_id and not saved_assistant:
            cleanup_task = asyncio.get_running_loop().create_task(
                _handle_aborted_stream(
                    user_id=user_id,
                    session_id=session_id,
                    user_msg_id=saved_user_msg_id,
                    content=full_content,
                    reasoning=full_reasoning,
                    tool_calls=tool_calls_list,
                )
            )
            # 强引用保护：任务完成前不被 GC 回收（与 dev_runner._BACKGROUND_TASKS 同策略）
            _BACKGROUND_TASKS.add(cleanup_task)
            cleanup_task.add_done_callback(_BACKGROUND_TASKS.discard)
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                # 外层取消已传播；cleanup_task 仍在事件循环中独立运行，尽力完成
                pass


async def _get_owned_conversation(db, user_id: int, session_id: int):
    """
    查询归属当前用户且未被删除的会话，不存在或无权访问时返回 None。
    """
    from sqlalchemy import select

    result = await db.execute(
        select(Conversation).where(
            Conversation.id == session_id,
            Conversation.user_id == user_id,
            Conversation.status == 1,
        )
    )
    return result.scalar_one_or_none()


async def _save_user_message(db, user_id: int, session_id: int, message: str) -> int | None:
    """
    请求开始时立即保存用户消息到指定会话并提交。

    原来的实现把用户消息和AI回复放在流结束（done）时一次性保存，
    生成中途一旦被中断（用户停止/切换会话/关闭抽屉/断网/后端异常），
    用户消息会连同AI回复一起丢失，表现为"问的问题消失了"。
    改为先落库用户消息，即使流中断，用户的提问也保留在会话历史中。

    注意：这里主动 commit 而不是依赖 get_db 的收尾提交——
    客户端断开时生成器被取消，get_db 的 commit 不会执行，flush 的数据会被回滚。

    Returns:
        保存的消息 id（供流中断时删除孤立的用户消息），失败/会话不存在时为 None。
    """
    try:
        conv = await _get_owned_conversation(db, user_id, session_id)
        if not conv:
            # 会话不存在或无权访问，静默跳过
            return None

        msg = ConversationMessage(
            conversation_id=session_id,
            role="user",
            content=message,
        )
        db.add(msg)
        conv.updated_at = datetime.now()

        await db.commit()
        return msg.id
    except Exception:
        # 保存失败不影响对话功能，仅记录日志
        logger.warning("保存用户消息失败: session=%s", session_id, exc_info=True)
        return None


async def _handle_aborted_stream(
    user_id: int,
    session_id: int,
    user_msg_id: int | None,
    content: str,
    reasoning: str,
    tool_calls: list[str],
) -> None:
    """
    流中断（客户端断开/异常）且 assistant 消息未保存时的兜底清理。

    用户消息已在请求开始时落库，此时若不管，会话里会留下
    一条没有任何 AI 回复的孤立用户提问。两种处理：
    - 已生成部分回复 → 保存带"对话已中断"标记的占位 assistant 消息，
      用户重开会话仍能看到这次对话的上下文（含已生成的内容）
    - 完全没生成回复 → 删除孤立的用户消息，保持会话干净

    注意：必须用独立的新 session 而非请求级 db——本任务由 finally 里的
    shield 脱离取消作用域运行，请求结束时 get_db 已关闭原 session，
    复用会抛 InvalidRequestError 导致清理静默失败。
    """
    try:
        async with async_session_factory() as cleanup_db:
            # 保留标准与 done 路径三条件一致（C3）：
            # 只要生成了部分回复 OR 有思考过程 OR 执行过工具调用，
            # 就保存占位消息保住用户提问（如报表工具跑 100-150s 中途断连，
            # content 为空但工具已执行，此时删除用户提问会丢掉一次有效提问）；
            # 三条件全空才删除孤立的用户消息，保持会话干净
            if content.strip() or reasoning or tool_calls:
                await _save_assistant_message(
                    db=cleanup_db,
                    user_id=user_id,
                    session_id=session_id,
                    assistant_content=content + "\n\n（对话已中断，以上为部分内容）",
                    reasoning=reasoning,
                    tool_calls=tool_calls,
                )
            elif user_msg_id is not None:
                await _delete_message(cleanup_db, user_id, session_id, user_msg_id)
    except Exception:
        logger.warning(
            "流中断清理失败: session=%s msg=%s", session_id, user_msg_id, exc_info=True
        )


async def _delete_message(db, user_id: int, session_id: int, message_id: int) -> None:
    """
    删除指定会话中的单条用户消息（流中断且无任何回复时的孤儿清理）。
    只允许删除归属当前用户会话中的消息，防止越权。
    """
    from sqlalchemy import delete

    try:
        conv = await _get_owned_conversation(db, user_id, session_id)
        if not conv:
            return
        await db.execute(
            delete(ConversationMessage).where(
                ConversationMessage.id == message_id,
                ConversationMessage.conversation_id == session_id,
                ConversationMessage.role == "user",
            )
        )
        await db.commit()
        logger.info(
            "流中断清理: 删除无回复的孤立用户消息 session=%s msg=%s",
            session_id, message_id,
        )
    except Exception:
        logger.warning("删除孤立用户消息失败: session=%s msg=%s", session_id, message_id, exc_info=True)


async def _save_assistant_message(
    db,
    user_id: int,
    session_id: int,
    assistant_content: str,
    reasoning: str,
    tool_calls: list[str],
):
    """
    对话完成（done 事件）时保存AI回复到指定会话。

    用户消息已在请求开始时保存，这里只写入 assistant 消息。
    主动 commit，避免生成器结束时依赖 get_db 收尾提交被中断跳过。

    Returns:
        bool: 是否保存成功（会话不存在视为失败；调用方据此决定
        saved_assistant 置位，保存失败时 finally 兜底清理仍会执行）
    """
    try:
        conv = await _get_owned_conversation(db, user_id, session_id)
        if not conv:
            # 会话不存在或无权访问，静默跳过
            return False

        # 保存AI回复（含推理过程和工具调用记录）
        db.add(ConversationMessage(
            conversation_id=session_id,
            role="assistant",
            content=assistant_content,
            reasoning=reasoning if reasoning else None,
            tool_calls=tool_calls if tool_calls else None,
        ))
        conv.updated_at = datetime.now()

        await db.commit()
        return True
    except Exception:
        # 保存失败不影响对话功能，仅记录日志
        logger.warning("保存AI回复失败: session=%s", session_id, exc_info=True)
        return False


@router.post("/chat")
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    AI 助教对话接口（SSE 流式返回）

    请求体：
    - message: 当前消息（必填）
    - history: 历史对话消息列表
    - context: 年级/学科上下文（可选）
    - session_id: 关联的会话ID（可选，传入后自动持久化消息）

    返回：Server-Sent Events 流

    限流：每用户同时最多 2 个在途请求，每小时最多 60 次调用。
    """
    uid = current_user.id

    # 并发数限制
    if _chat_concurrent[uid] >= _CHAT_MAX_CONCURRENT_PER_USER:
        raise HTTPException(
            status_code=429,
            detail=f"同时进行的对话过多（最多 {_CHAT_MAX_CONCURRENT_PER_USER} 个），请等待当前对话完成",
        )

    # 频率限制（仅做前置检查与过滤，计数在 _stream_chat 真正开始时才 append，
    # 避免"流创建后立即失败"（如 LLM 500）仍占用本小时配额）
    now = time.time()
    timestamps = _chat_rate_timestamps[uid]
    timestamps[:] = [t for t in timestamps if now - t < _CHAT_RATE_WINDOW_SECONDS]
    if len(timestamps) >= _CHAT_MAX_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail="本小时对话次数已达上限，请稍后再试",
        )

    return StreamingResponse(
        _stream_chat(
            message=req.message,
            history=[h.model_dump() for h in req.history],
            context=req.context.model_dump() if req.context else None,
            db=db,
            user_id=current_user.id,
            session_id=req.session_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/explain")
async def explain(
    req: ExplainRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    完整讲解直连接口（不经过 Agent 工具链，直接调用 ExplainService）

    一次性返回完整讲解文本 + 一道思考题，前端展示讲解并提供思考题作答入口，
    作答结果通过 /explain/check 判题。

    若请求携带 question_id：先校验题目归属（只能讲解自己的作业题），
    再把该题的切割原图（含题干）转成 base64 data URL 喂给视觉模型，
    让 LLM 真正"看到"题目——纯文本上下文只有批改结果字段，不含题干原文。
    归属校验失败/图片读取失败时静默降级为纯文本讲解，不阻断主流程。

    返回：{knowledge_points, explanation, thinking_question}
    """
    from app.services.explain_service import ExplainService
    from app.services.file_upload import MIME_MAP, StorageService

    if not req.exercise_content.strip():
        raise HTTPException(status_code=400, detail="题目内容不能为空")

    # 题目原图 → 多模态讲解输入（可选增强，失败不影响主流程）
    images: list[str] = []
    if req.question_id:
        try:
            from app.models.question import Question

            q = await db.get(Question, req.question_id)
            if q is not None:
                # 归属校验：题目必须属于当前用户的作业，防止越权读取他人题目图片
                from app.models.assignment import Assignment

                assignment = await db.get(Assignment, q.assignment_id)
                if assignment is not None and assignment.creator_id == current_user.id:
                    storage = StorageService()
                    img_bytes = await storage.get_file_bytes(q.image_url)
                    if img_bytes:
                        ext = q.image_url.rsplit(".", 1)[-1].lower() if "." in q.image_url else "png"
                        mime = MIME_MAP.get(ext, "image/png")
                        import base64

                        images.append(
                            f"data:{mime};base64,{base64.b64encode(img_bytes).decode()}"
                        )
        except Exception as e:
            # 图片链路任何异常（查库/读文件/编码）都降级为纯文本讲解，
            # 讲解功能不能因增强功能失败而不可用
            logger.warning("加载题目图片失败，降级纯文本讲解: %s", e)

    service = ExplainService()
    try:
        return await service.explain_full(
            exercise_content=req.exercise_content,
            subject=req.subject,
            explanation_style=req.explanation_style,
            strict_level=req.strict_level,
            images=images,
        )
    except Exception as e:
        # 不把内部异常细节（可能含 LLM 请求内容片段）透传给前端（Alt6），
        # 完整堆栈记入服务端日志便于排查
        logger.exception("讲解生成失败: %s", e)
        raise HTTPException(status_code=500, detail="讲解生成失败，请稍后重试")


@router.post("/feedback")
async def feedback(
    req: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    记录讲解反馈并更新知识点掌握状态（专用直连接口）。

    前端讲解卡片上的"听懂/部分听懂/没听懂"反馈直接落到知识状态库，
    不再伪装成一条 Agent 聊天消息走 /chat 全链路——
    原实现会让 LLM 跑一轮完整 ReAct（耗时且反馈是否真正落库取决于
    Agent 是否恰好调用工具），且前端 fire-and-forget 不校验结果。
    """
    from app.services.knowledge_tracker import KnowledgeTracker, parse_feedback_level

    if not req.knowledge_point.strip():
        raise HTTPException(status_code=400, detail="知识点不能为空")

    # 与 AgentTools.record_mastery_feedback 共用同一套映射
    # （knowledge_tracker.parse_feedback_level）：完全听懂 +1、没听懂 -1、
    # 部分听懂 0，避免两处记录口径不一致（Alt3）
    mastery_change, behavior_type = parse_feedback_level(req.feedback_level)

    try:
        tracker = KnowledgeTracker(db)
        count = await tracker.update(
            user_id=current_user.id,
            knowledge_points=[{
                "point_name": req.knowledge_point,
                "subject": "通用",
                "mastery_change": mastery_change,
                "behavior_type": behavior_type,
            }],
            update_source="题目讲解",
            related_id=req.question_id,
        )
        return {"updated": True, "updated_count": count}
    except Exception as e:
        # 不把内部异常细节透传给前端（Alt6），完整堆栈记入服务端日志
        logger.exception("记录讲解反馈失败: %s", e)
        raise HTTPException(status_code=500, detail="反馈记录失败，请稍后重试")


@router.post("/explain/check")
async def explain_check(
    req: ExplainCheckRequest,
    current_user: User = Depends(get_current_user),
):
    """
    思考题作答判题接口

    LLM 先根据原题上下文自行解出思考题，再对比学生回答判定对错，
    参考答案全程不下发前端，避免答案泄露。

    返回：{verdict: correct|partial|wrong, feedback}
    """
    from app.services.explain_service import ExplainService

    if not req.user_answer.strip():
        raise HTTPException(status_code=400, detail="回答内容不能为空")
    if not req.thinking_question.strip():
        raise HTTPException(status_code=400, detail="思考题不能为空")

    service = ExplainService()
    try:
        return await service.check_thinking_answer(
            exercise_content=req.exercise_content,
            thinking_question=req.thinking_question,
            user_answer=req.user_answer,
            subject=req.subject,
        )
    except Exception as e:
        # 不把内部异常细节透传给前端（Alt6），完整堆栈记入服务端日志
        logger.exception("判题失败: %s", e)
        raise HTTPException(status_code=500, detail="判题失败，请稍后重试")
