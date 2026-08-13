"""
数据看板 - Token 用量统计 Schema
"""

from pydantic import BaseModel


class DailyUsageItem(BaseModel):
    """单日用量聚合"""
    date: str               # YYYY-MM-DD
    calls: int              # 当日调用次数
    prompt_tokens: int      # 当日输入 Token
    completion_tokens: int  # 当日输出 Token
    total_tokens: int       # 当日总 Token


class UsageSummary(BaseModel):
    """区间汇总指标"""
    days: int                # 统计天数
    total_calls: int         # 累计调用次数
    total_tokens: int        # 累计 Token 消耗
    avg_daily_calls: float   # 日均调用次数
    avg_daily_tokens: float  # 日均 Token 消耗量


class TokenUsageResponse(BaseModel):
    summary: UsageSummary
    daily: list[DailyUsageItem]
