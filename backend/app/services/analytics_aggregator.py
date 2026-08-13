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
from sqlalchemy import select, func, or_

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
        # k.get("name", ...) 兜底：knowledge_points 是 LLM 自由返回的 JSON，
        # 部分题目存的是 {"知识点": "xxx"} 等无 name 键的变体，直接 k["name"]
        # 会 KeyError 把整个学情接口打成 500
        return [k.get("name", str(k)) if isinstance(k, dict) else str(k) for k in kps]
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
        subject: str | None = None,
        semester: str | None = None,
        usage_months: list[str] | None = None,
        assignment_id: int | None = None,
    ) -> dict:
        """
        按科目统计已完成作业数量、题目数、总得分与得分率。

        返回示例：
        {
            "total": 25,
            "subject_stats": [
                {
                    "subject": "数学", "count": 8,
                    "question_count": 120, "total_score": 900.0,
                    "total_full": 1200.0, "score_rate": 0.75,
                },
                {"subject": "英语", "count": 6, "question_count": 80, ...},
            ]
        }

        说明：score_rate = total_score / total_full（科目内所有已打分题目合计），
        total_full 为 0（无已打分题目）时 score_rate 为 None。
        """
        base_filter = [
            Assignment.creator_id == user_id,
            Assignment.status == AssignmentStatus.COMPLETED,
        ]
        if grade:
            base_filter.append(Assignment.grade == grade)
        if subject:
            base_filter.append(Assignment.subject == subject)
        if semester:
            base_filter.append(Assignment.semester == semester)
        if usage_months:
            base_filter.append(Assignment.usage_month.in_(usage_months))
        if assignment_id:
            base_filter.append(Assignment.id == assignment_id)

        # 按科目分组统计：作业数 + 题目得分聚合。
        # 用 outerjoin 保证无题目的作业也计入 count；
        # 注意：join 后每道题一行，作业数必须用 count(distinct id)，否则会按题目行重复计数；
        # 题目得分条件不能在 WHERE 中过滤（会把无题目的作业行滤掉），
        # 得分率在 Python 端按 total_full 是否为 0 兜底。
        #
        # 题目数口径（A2-8）：必须是"叶子题目"——父题容器（存在子题指向它的行）
        # 只是组织节点，不计入题目数。与作业明细/报告/看板各处口径一致，
        # 否则同一份作业的题目数在不同板块显示不同。
        parent_container_ids = (
            select(Question.parent_id)
            .where(Question.parent_id.isnot(None))
            .group_by(Question.parent_id)
            .subquery()
        )
        # 注意 NOT IN 陷阱：outerjoin 无题目的作业行 Question.id 为 NULL，
        # `id NOT IN (subq)` 对 NULL 求值为 UNKNOWN 会被 WHERE 滤掉，
        # 因此必须用 or_ 保留 NULL 行（count/sum 天然忽略 NULL，不影响统计）
        stmt = (
            select(
                Assignment.subject,
                func.count(func.distinct(Assignment.id)),
                func.count(Question.id),
                func.coalesce(func.sum(Question.score), 0.0),
                func.coalesce(func.sum(Question.full_score), 0.0),
            )
            .outerjoin(Question, Question.assignment_id == Assignment.id)
            .where(
                *base_filter,
                or_(Question.id.is_(None), ~Question.id.in_(parent_container_ids)),
            )
            .group_by(Assignment.subject)
            .order_by(func.count(func.distinct(Assignment.id)).desc())
        )
        result = await self.db.execute(stmt)
        rows = result.all()

        subject_stats = []
        for row in rows:
            count, question_count, total_score, total_full = row[1], row[2], float(row[3]), float(row[4])
            subject_stats.append({
                "subject": row[0],
                "count": count,
                "question_count": question_count,
                "total_score": total_score,
                "total_full": total_full,
                "score_rate": round(total_score / total_full, 4) if total_full > 0 else None,
            })
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
        usage_months: list[str] | None = None,
        assignment_id: int | None = None,
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
                "usage_month": "2026-04",
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
                Assignment.usage_month,
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
        if usage_months:
            stmt = stmt.where(Assignment.usage_month.in_(usage_months))
        if assignment_id:
            stmt = stmt.where(Assignment.id == assignment_id)

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
                "usage_month": row[5],
                # created_at 转为 ISO 字符串：工具执行层会对结果做 json.dumps，
                # datetime 对象不可序列化会导致整个工具调用失败
                "created_at": row[6].isoformat() if row[6] else None,
                "score_rate": round(float(row[7]) if row[7] else 0.0, 4),
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
        usage_months: list[str] | None = None,
        semester: str | None = None,
    ) -> list[dict]:
        """
        聚合知识点考察频次和得分率。

        参数：
        - grade, subject: 按年级/科目筛选作业
        - assignment_ids: 指定具体作业 ID 列表（优先级最高）
        - usage_months: 按使用月份筛选（兼容值列表，如 ['2026-04', '4']）
        - semester: 按学期筛选（assignment_ids 存在时被覆盖，不生效）

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
            # 否则按年级/科目/使用月份/学期筛选
            if grade:
                filter_conds.append(Assignment.grade == grade)
            if subject:
                filter_conds.append(Assignment.subject == subject)
            if usage_months:
                filter_conds.append(Assignment.usage_month.in_(usage_months))
            if semester:
                filter_conds.append(Assignment.semester == semester)

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

    # ==================== 子板块4：精确统计（报告用）====================

    async def get_precise_stats(
        self,
        user_id: int,
        grade: str | None = None,
        subject: str | None = None,
        usage_months: list[str] | None = None,
        assignment_id: int | None = None,
    ) -> dict:
        """
        精确统计作业数、题目数、得分率、错题数、作业明细、题型分布。
        供 generate_analysis_report 工具调用，避免 SQL 直接写在工具层。

        返回：
        {
            "total_assignments": int,
            "total_questions": int,
            "total_score": float,
            "total_full_score": float,
            "correct_rate": float,
            "error_count": int,
            "assignment_details": [{"id", "name", "question_count", "total_score", "total_full", "score_rate", "ai_summary"}],
            "type_distribution": {assignment_id: {question_type: count}},
        }
        """
        base_conditions = [
            Assignment.creator_id == user_id,
            Assignment.status == AssignmentStatus.COMPLETED,
        ]
        if grade:
            base_conditions.append(Assignment.grade == grade)
        if subject:
            base_conditions.append(Assignment.subject == subject)
        if usage_months:
            base_conditions.append(Assignment.usage_month.in_(usage_months))
        if assignment_id:
            base_conditions.append(Assignment.id == assignment_id)

        # 作业总数
        stmt = select(func.count(Assignment.id)).where(*base_conditions)
        total_assignments = (await self.db.execute(stmt)).scalar() or 0

        # 题目总数、总得分、总满分（只统计有效打分的题目）
        stmt = (
            select(
                func.count(Question.id),
                func.coalesce(func.sum(Question.score), 0.0),
                func.coalesce(func.sum(Question.full_score), 0.0),
            )
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(
                *base_conditions,
                Question.score.isnot(None),
                Question.full_score.isnot(None),
                Question.full_score > 0,
            )
        )
        row = (await self.db.execute(stmt)).one()
        total_questions = row[0] or 0
        total_score = float(row[1] or 0)
        total_full_score = float(row[2] or 0)
        correct_rate = total_score / total_full_score if total_full_score > 0 else 0.0

        # 错题数（得分率 < 60%），已在 WHERE 中排除 full_score <= 0 的题目，避免除零
        # 使用乘法避免除法表达式在某些 SQLAlchemy 方言下的潜在注入风险
        error_conditions = base_conditions + [
            Question.score.isnot(None),
            Question.full_score.isnot(None),
            Question.full_score > 0,
            Question.score < Question.full_score * 0.6,
        ]
        stmt = (
            select(func.count(Question.id))
            .join(Assignment, Question.assignment_id == Assignment.id)
            .where(*error_conditions)
        )
        error_count = (await self.db.execute(stmt)).scalar() or 0

        # 作业明细
        detail_stmt = (
            select(
                Assignment.id,
                Assignment.name,
                Assignment.ai_summary,
                func.count(Question.id).label("question_count"),
                func.coalesce(func.sum(Question.score), 0.0).label("total_score"),
                func.coalesce(func.sum(Question.full_score), 0.0).label("total_full"),
            )
            .join(Question, Assignment.id == Question.assignment_id)
            .where(
                *base_conditions,
                Question.score.isnot(None),
                Question.full_score.isnot(None),
                Question.full_score > 0,
            )
            .group_by(Assignment.id)
            .order_by(Assignment.created_at.asc())
        )
        detail_rows = (await self.db.execute(detail_stmt)).all()

        # 每个作业的题型分布
        type_stmt = (
            select(
                Assignment.id,
                Question.question_type,
                func.count(Question.id).label("type_count"),
            )
            .join(Question, Assignment.id == Question.assignment_id)
            .where(
                *base_conditions,
                Question.question_type.isnot(None),
                Question.question_type != "",
            )
            .group_by(Assignment.id, Question.question_type)
        )
        type_rows = (await self.db.execute(type_stmt)).all()
        type_map: dict[int, dict[str, int]] = {}
        for aid, q_type, count in type_rows:
            type_map.setdefault(aid, {})[q_type] = count

        assignment_details = []
        for row in detail_rows:
            aid, name, ai_summary, q_count, t_score, t_full = row
            assignment_details.append({
                "id": aid,
                "name": name,
                "question_count": q_count,
                "total_score": float(t_score),
                "total_full": float(t_full),
                "score_rate": round(float(t_score) / float(t_full), 4) if float(t_full) > 0 else 0.0,
                "ai_summary": ai_summary,
                "question_types": type_map.get(aid, {}),
            })

        return {
            "total_assignments": total_assignments,
            "total_questions": total_questions,
            "total_score": total_score,
            "total_full_score": total_full_score,
            "correct_rate": correct_rate,
            "error_count": error_count,
            "assignment_details": assignment_details,
        }
