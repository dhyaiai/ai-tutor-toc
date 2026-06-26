import json
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.ai import ChatRequest

router = APIRouter(prefix="/ai-tutor", tags=["ai-tutor"])


async def _stream_chat(
    message: str,
    history: list[dict],
    context: dict | None,
    db,
    user_id: int,
):
    """SSE 流式返回 Agent 对话。"""
    from app.services.agent.agent_executor import AgentExecutor

    executor = AgentExecutor(db, user_id)

    async for event in executor.run(
        message=message,
        history=history,
        context=context,
    ):
        yield f"event: message\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return StreamingResponse(
        _stream_chat(
            message=req.message,
            history=[h.model_dump() for h in req.history],
            context=req.context.model_dump() if req.context else None,
            db=db,
            user_id=current_user.id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
