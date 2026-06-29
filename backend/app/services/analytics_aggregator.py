"""
学情聚合计算服务。

提供三个子板块的数据查询：
- 作业统计：按科目统计作业数量
- 学生学期看板：按时间排序的作业得分率趋势
- 知识点热力图：知识点考察频次和得分率聚合

SQL 聚合查询，返回 dict 供 API 路由层包装为 Pydantic Schema。
"""

import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.assignment import Assignment, AssignmentStatus
from app.models.question import Question

logger = logging.getLogger(__name__)


def _extract_kp_names(kps) -> list[str]:
    """
    从 JSON 字段中提取知识点名称列表。

    兼容三种存储格式：
    - list[dict]: [{"name": "分数加减法", "category": "计算", ...}, ...]
    - list[str]: ["分数加减法", "分数比较"]
    - dict: {"name": "分数加减法", ...}
    """
    if not kps:
        return []
    if isinstance(kps, list):
        return [k["name"] if isinstance(k, dict) else str(k) for k in kps]
    if isinstance(kps, dict):
        return [kps.get("name", str(kps))]
    return []


class AnalyticsAggregator:
    """学情聚合器"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ==================== 子板块1：作业统计 ====================

    async def get_homework_stats(
        self,
        user_id: int,
        grade: str | None = None,
        semester: str | None = None,
    ) -> dict:
        """
        按科目统计已完成作业数量。

        返回示例：
        {
            "total": 25,
            "subject_stats": [
                {"subject": "数学", "count": 8},
                {"subject": "英语", "count": 6},
            ]
        }
        """
        base_filter = [
            Assignment.creator_id == user_id,
            Assignment.status == AssignmentStatus.COMPLETED,
        ]
        if grade:
            base_filter.append(Assignment.grade == grade)
        if semester:
            base_filter.append(Assignment.semester == semester)

        # 按科目分组统计
        stmt = (
            select(
                Assignment.subject,
                func.count(Assignment.id),
            )
            .where(*base_filter)
            .group_by(Assignment.subject)
            .order_by(func.count(Assignment.id).desc())
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        subject_stats = [
            {"subject": row[0], "count": row[1]} for row in rows
        ]
        total = sum(s["count"] for s in subject_stats)

        return {
            "total": total,
            "subject_stats": subject_stats,
        }

    # ==================== 子板块2：学生学期看板 ====================

    async def get_student_dashboard(
        self,
        user_id: int,
        grade: str | None = None,
        subject: str | None = None,
        semester: str | None = None,
    ) -> list[dict]:
        """
        获取每份已完成作业的得分率，按创建时间排序。

        得分率计算方式：SUM(q.score) / SUM(q.full_score)，
        排除 full_score 为 0 或 NULL 的题目。

        返回示例：
        [
            {
                "id": 1, "name": "期中考试", "grade": "高一",
                "subject": "数学", "semester": "上学期",
                "created_at": datetime(...), "score_rate": 0.85
            },
        ]
        """
        # 子查询：每份作业的 SUM(score) 和 SUM(full_score)
        subq = (
            select(
                Question.assignment_id,
                func.sum(Question.score).label("total_score"),
                func.sum(Question.full_score).label("total_full"),
            )
            .where(
                Question.score.isnot(None),
                Question.full_score.isnot(None),
                Question.full_score > 0,
            )
            .group_by(Question.assignment_id)
            .subquery()
        )

        stmt = (
            select(
                Assignment.id,
                Assignment.name,
                Assignment.grade,
                Assignment.subject,
                Assignment.semester,
                Assignment.created_at,
                (func.coalesce(subq.c.total_score, 0) /
                 func.nullif(subq.c.total_full, 0)).label("score_rate"),
            )
            .outerjoin(subq, Assignment.id == subq.c.assignment_id)
            .where(
                Assignment.creator_id == user_id,
                Assignment.status == AssignmentStatus.COMPLETED,
            )
        )
        if grade:
            stmt = stmt.where(Assignment.grade == grade)
        if subject:
            stmt = stmt.where(Assignment.subject == subject)
        if semester:
            stmt = stmt.where(Assignment.semester == semester)

        # 按创建时间升序排列
        stmt = stmt.order_by(Assignment.created_at.asc())

        result = await self.db.execute(stmt)
        rows = result.all()

        return [
            {
                "id": row[0],
                "name": row[1],
                "grade": row[2],
                "subject": row[3],
                "semester": row[4],
                "created_at": row[5],
                "score_rate": round(float(row[6]) if row[6] else 0.0, 4),
            }
            for row in rows
        ]

    # ==================== 子板块3：知识点热力图 ====================

    async def get_knowledge_heatmap(
        self,
        user_id: int,
        grade: str | None = None,
        subject: str | None = None,
        assignment_ids: list[int] | None = None,
    ) -> list[dict]:
        """
        聚合知识点考察频次和得分率。

        参数：
        - grade, subject: 按年级/科目筛选作业
        - assignment_ids: 指定具体作业 ID 列表（优先级最高）

        返回示例：
        [
            {
                "knowledge_point": "二次函数",
                "frequency": 15,
                "score_rate": 0.72,
            },
        ]
        """
        # 构建过滤条件
        filter_conds = [
            Assignment.creator_id == user_id,
            Assignment.status == AssignmentStatus.COMPLETED,
            Question.knowledge_points.isnot(None),
            Question.score.isnot(None),
            Question.full_score.isnot(None),
            Question.full_score > 0,
        ]

        if assignment_ids:
            # 指定作业 ID 列表时，优先使用
            filter_conds.append(Assignment.id.in_(assignment_ids))
        else:
            # 否则按年级/科目筛选
            if grade:
                filter_conds.append(Assignment.grade == grade)
            if subject:
                filter_conds.append(Assignment.subject == subject)

        # 查询题目知识点、得分、满分
        stmt = (
            select(
                Question.knowledge_points,
                Question.score,
                Question.full_score,
            )
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(*filter_conds)
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        # Python 端聚合：按知识点累加得分和满分
        # kp_data[kp_name] = {"total_score": float, "total_full": float, "count": int}
        kp_data: dict[str, dict] = {}

        for kps, score, full_score in rows:
            names = _extract_kp_names(kps)
            for name in names:
                if name not in kp_data:
                    kp_data[name] = {"total_score": 0.0, "total_full": 0.0, "count": 0}
                kp_data[name]["total_score"] += float(score)
                kp_data[name]["total_full"] += float(full_score)
                kp_data[name]["count"] += 1

        # 计算得分率并排序（按频次降序）
        items = sorted(
            [
                {
                    "knowledge_point": kp,
                    "frequency": data["count"],
                    "score_rate": round(
                        data["total_score"] / data["total_full"], 4
                    ) if data["total_full"] > 0 else 0.0,
                }
                for kp, data in kp_data.items()
            ],
            key=lambda x: x["frequency"],
            reverse=True,
        )

        return items
