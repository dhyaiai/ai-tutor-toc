"""
数据看板 API

提供 LLM Token 用量统计：按日聚合的调用次数与 Token 消耗，
以及区间汇总指标（日均 Token 消耗量 / 日均调用次数等）。
数据来源为 llm_usage_tracker 自动写入的 llm_usage_logs 表。
"""

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import get_current_admin
from app.models.user import User
from app.models.llm_usage import LlmUsageLog
from app.schemas.usage import DailyUsageItem, UsageSummary, TokenUsageResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/token-usage", response_model=TokenUsageResponse)
async def get_token_usage(
    days: int = Query(30, ge=1, le=180, description="统计最近 N 天"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """按日聚合最近 N 天的 Token 用量与调用量（缺失日期补 0，保证图表连续）"""
    today = date.today()
    start_date = today - timedelta(days=days - 1)

    day_expr = func.date(LlmUsageLog.created_at).label("day")
    result = await db.execute(
        select(
            day_expr,
            func.count(LlmUsageLog.id),
            func.coalesce(func.sum(LlmUsageLog.prompt_tokens), 0),
            func.coalesce(func.sum(LlmUsageLog.completion_tokens), 0),
            func.coalesce(func.sum(LlmUsageLog.total_tokens), 0),
        )
        .where(LlmUsageLog.created_at >= datetime.combine(start_date, time.min))
        .group_by(day_expr)
        .order_by(day_expr)
    )

    # func.date 在 MySQL 返回 date 对象，统一归一化为 YYYY-MM-DD
    by_day: dict[str, tuple[int, int, int, int]] = {}
    for day, calls, prompt, completion, total in result.all():
        key = day.isoformat() if hasattr(day, "isoformat") else str(day)[:10]
        by_day[key] = (int(calls), int(prompt), int(completion), int(total))

    # 补全区间内的所有日期（无数据的日期补 0）
    daily: list[DailyUsageItem] = []
    for i in range(days):
        d = (start_date + timedelta(days=i)).isoformat()
        calls, prompt, completion, total = by_day.get(d, (0, 0, 0, 0))
        daily.append(DailyUsageItem(
            date=d,
            calls=calls,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        ))

    total_calls = sum(item.calls for item in daily)
    total_tokens = sum(item.total_tokens for item in daily)
    # 分母用有数据的天数（而非请求的 days），避免上线初期被补零日稀释失真
    active_days = max(sum(1 for item in daily if item.calls > 0), 1)
    summary = UsageSummary(
        days=days,
        total_calls=total_calls,
        total_tokens=total_tokens,
        avg_daily_calls=round(total_calls / active_days, 1),
        avg_daily_tokens=round(total_tokens / active_days, 1),
    )
    return TokenUsageResponse(summary=summary, daily=daily)
