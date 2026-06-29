"""
学情分析 API 路由（v1）

三个子板块：
1. GET /analytics/homework-stats      — 作业统计（按科目统计作业数量）
2. GET /analytics/student-dashboard  — 学生学期看板（得分率趋势 + 作业情况表）
3. GET /analytics/knowledge-heatmap  — 知识点热力图（考察频次 + 得分率）
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.analytics import (
    HomeworkStatsResponse,
    DashboardResponse,
    DashboardItem,
    KnowledgeHeatmapResponse,
    KnowledgeHeatmapItem,
)
from app.services.analytics_aggregator import AnalyticsAggregator

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ==================== 子板块1：作业统计 ====================

@router.get("/homework-stats", response_model=HomeworkStatsResponse)
async def get_homework_stats(
    grade: str | None = Query(None, description="年级筛选"),
    semester: str | None = Query(None, description="学期筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    按科目统计已完成的作业数量。

    - 支持按年级、学期筛选
    - 返回各科目作业数量及总数
    """
    agg = AnalyticsAggregator(db)
    result = await agg.get_homework_stats(
        user_id=current_user.id,
        grade=grade,
        semester=semester,
    )
    return result


# ==================== 子板块2：学生学期看板 ====================

@router.get("/student-dashboard", response_model=DashboardResponse)
async def get_student_dashboard(
    grade: str | None = Query(None, description="年级筛选"),
    subject: str | None = Query(None, description="科目筛选"),
    semester: str | None = Query(None, description="学期筛选"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取每份已完成作业的得分率，按创建时间排序。

    - 支持按年级、科目、学期筛选
    - 得分率 = 该作业所有题目的 SUM(score) / SUM(full_score)
    - 按 created_at 升序排列，供前端折线图使用
    """
    agg = AnalyticsAggregator(db)
    items = await agg.get_student_dashboard(
        user_id=current_user.id,
        grade=grade,
        subject=subject,
        semester=semester,
    )
    return {"items": items}


# ==================== 子板块3：知识点热力图 ====================

@router.get("/knowledge-heatmap", response_model=KnowledgeHeatmapResponse)
async def get_knowledge_heatmap(
    grade: str | None = Query(None, description="年级筛选"),
    subject: str | None = Query(None, description="科目筛选"),
    assignment_ids: list[int] | None = Query(
        None, description="指定作业 ID 列表，传入多个值如 ?assignment_ids=1&assignment_ids=2"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    聚合知识点考察频次和得分率。

    - 支持按年级、科目筛选，或直接指定作业 ID 列表
    - 频次 = 该知识点在已选作业题目中出现的次数
    - 得分率 = 该知识点所有题目的 SUM(score) / SUM(full_score)
    - 结果按频次降序排列
    """
    agg = AnalyticsAggregator(db)
    items = await agg.get_knowledge_heatmap(
        user_id=current_user.id,
        grade=grade,
        subject=subject,
        assignment_ids=assignment_ids,
    )
    return {"items": items}
