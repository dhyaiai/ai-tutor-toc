"""
学情聚合计算服务。

提供：
- 概览统计（平均分、错误率、各科对比）
- 分数趋势（按月）
- 薄弱知识点分析

SQL 聚合查询 + 可选 Redis 缓存
"""

import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.assignment import Assignment, AssignmentStatus
from app.models.question import Question

logger = logging.getLogger(__name__)


class AnalyticsAggregator:
    """学情聚合器"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_overview(
        self,
        user_id: int,
        grade: str | None = None,
        subject: str | None = None,
    ) -> dict:
        """获取学情概览"""
        base_filter = [
            Assignment.creator_id == user_id,
            Assignment.status == AssignmentStatus.COMPLETED,
        ]
        if grade:
            base_filter.append(Assignment.grade == grade)
        if subject:
            base_filter.append(Assignment.subject == subject)

        # Total assignments
        count_stmt = select(func.count()).select_from(Assignment).where(*base_filter)
        total = (await self.db.execute(count_stmt)).scalar() or 0

        # Question stats
        q_stmt = (
            select(
                func.count(Question.id),
                func.avg(Question.score),
                func.count().filter(Question.score < Question.full_score),
            )
            .select_from(Question)
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(*base_filter)
        )
        q_result = await self.db.execute(q_stmt)
        total_q, avg_score, err_count = q_result.one()

        total_q = total_q or 0
        avg_score = float(avg_score) if avg_score else 0.0
        err_count = err_count or 0
        error_rate = err_count / total_q if total_q > 0 else 0.0

        # Per-subject breakdown
        subj_stmt = (
            select(
                Assignment.subject,
                func.avg(Assignment.total_score),
                func.count(Assignment.id),
            )
            .where(*base_filter)
            .group_by(Assignment.subject)
        )
        subj_result = await self.db.execute(subj_stmt)
        subject_averages = [
            {"subject": row[0], "average": float(row[1]) if row[1] else 0.0, "count": row[2]}
            for row in subj_result.all()
        ]

        return {
            "total_assignments": total,
            "average_score": round(avg_score, 2),
            "total_questions": total_q,
            "error_rate": round(error_rate, 4),
            "subject_averages": subject_averages,
        }

    async def get_score_trend(
        self,
        user_id: int,
        grade: str | None = None,
        subject: str | None = None,
        semester: str | None = None,
    ) -> list[dict]:
        """获取分数趋势（按月）"""
        filter_conds = [
            Assignment.creator_id == user_id,
            Assignment.status == AssignmentStatus.COMPLETED,
        ]
        if grade:
            filter_conds.append(Assignment.grade == grade)
        if subject:
            filter_conds.append(Assignment.subject == subject)
        if semester:
            filter_conds.append(Assignment.semester == semester)

        stmt = (
            select(
                Assignment.month,
                func.avg(Assignment.total_score),
                func.count(Assignment.id),
            )
            .where(*filter_conds)
            .group_by(Assignment.month)
            .order_by(Assignment.month)
        )
        result = await self.db.execute(stmt)
        return [
            {"month": row[0], "average_score": float(row[1]) if row[1] else 0.0, "count": row[2]}
            for row in result.all()
        ]

    async def get_weakness(
        self,
        user_id: int,
        grade: str | None = None,
        subject: str | None = None,
        semester: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """获取薄弱知识点 Top N"""
        filter_conds = [
            Assignment.creator_id == user_id,
            Question.score < Question.full_score,
            Question.knowledge_points.isnot(None),
        ]
        if grade:
            filter_conds.append(Assignment.grade == grade)
        if subject:
            filter_conds.append(Assignment.subject == subject)
        if semester:
            filter_conds.append(Assignment.semester == semester)

        stmt = (
            select(Question.knowledge_points, Question.score, Question.full_score)
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(*filter_conds)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        def _extract_kp_names(kps) -> list[str]:
            """Extract knowledge point names from JSON field, handling both string and dict formats."""
            if not kps:
                return []
            if isinstance(kps, list):
                return [k["name"] if isinstance(k, dict) else str(k) for k in kps]
            if isinstance(kps, dict):
                return [kps.get("name", str(kps))]
            return []

        # Count errors (first pass: only error questions)
        kp_errors: dict[str, int] = {}
        for kps, score, full_score in rows:
            for name in _extract_kp_names(kps):
                kp_errors[name] = kp_errors.get(name, 0) + 1

        # Count totals (second pass: ALL questions, including errors)
        kp_total: dict[str, int] = {}
        total_stmt = (
            select(Question.knowledge_points)
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(
                Assignment.creator_id == user_id,
                Question.knowledge_points.isnot(None),
            )
        )
        if grade:
            total_stmt = total_stmt.where(Assignment.grade == grade)
        if subject:
            total_stmt = total_stmt.where(Assignment.subject == subject)
        if semester:
            total_stmt = total_stmt.where(Assignment.semester == semester)

        total_result = await self.db.execute(total_stmt)
        for (kps,) in total_result.all():
            for name in _extract_kp_names(kps):
                kp_total[name] = kp_total.get(name, 0) + 1

        weak_points = sorted(
            [
                {
                    "knowledge_point": kp,
                    "error_count": kp_errors.get(kp, 0),
                    "total_count": kp_total.get(kp, 0),
                    "error_rate": round(kp_errors.get(kp, 0) / kp_total.get(kp, 1), 4),
                }
                for kp in kp_errors
            ],
            key=lambda x: x["error_rate"],
            reverse=True,
        )[:limit]

        return weak_points
