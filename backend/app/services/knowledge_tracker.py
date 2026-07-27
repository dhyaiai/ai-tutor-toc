"""
知识状态追踪服务

核心功能：
1. update: 根据学习行为批量更新知识点掌握度
2. query: 查询知识状态，支持薄弱点/进步点/汇总分析
3. 自动计算 mastery_level 并持久化到 user_knowledge_state 表

使用场景：
- 作业分析完成后自动调用（由 analysis_tasks 触发）
- 题目讲解反馈后调用（由 record_mastery_feedback 工具触发）
- 作文批改/口语测评完成后调用
"""

import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from app.models.knowledge_state import UserKnowledgeState

logger = logging.getLogger(__name__)


class KnowledgeTracker:
    """
    知识状态追踪器

    使用方式：
        tracker = KnowledgeTracker(db)
        await tracker.update(user_id=1, points=[...], source="作业分析")
        result = await tracker.query(user_id=1, subject="数学")
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def update(
        self,
        user_id: int,
        knowledge_points: list[dict],
        update_source: str,
        related_id: Optional[str] = None,
    ) -> int:
        """
        批量更新用户知识点掌握度

        Args:
            user_id: 用户ID
            knowledge_points: 知识点列表，每项含 point_name, subject, mastery_change, behavior_type
            update_source: 更新来源（作业分析/题目讲解/订正完成/练习测试/作文批改/口语测评）
            related_id: 关联ID（可选）

        Returns:
            更新的知识点数量
        """
        updated_count = 0

        for kp in knowledge_points:
            point_name = kp.get("point_name", "")
            subject = kp.get("subject", "通用")
            mastery_change = kp.get("mastery_change", 0)
            behavior_type = kp.get("behavior_type", "练习正确")

            if not point_name:
                continue

            # 查找已有记录
            result = await self.db.execute(
                select(UserKnowledgeState).where(
                    and_(
                        UserKnowledgeState.user_id == user_id,
                        UserKnowledgeState.subject == subject,
                        UserKnowledgeState.point_name == point_name,
                    )
                )
            )
            record = result.scalar_one_or_none()

            if record:
                # 更新已有记录
                new_score = record.mastery_score + mastery_change * 5
                # 限制在 0-100 范围内
                new_score = max(0, min(100, new_score))

                record.mastery_score = new_score
                record.mastery_level = UserKnowledgeState.calc_mastery_level(new_score)

                # 根据变化方向更新错误/正确计数
                if mastery_change < 0:
                    record.wrong_count += 1
                elif mastery_change > 0:
                    record.correct_count += 1

                record.last_practice_time = datetime.now()
                record.update_time = datetime.now()
            else:
                # 创建新记录
                base_score = 50 + mastery_change * 5
                base_score = max(0, min(100, base_score))

                # 判断初始行为类型来设置计数
                wrong = 1 if mastery_change < 0 else 0
                correct = 1 if mastery_change > 0 else 0

                record = UserKnowledgeState(
                    user_id=user_id,
                    subject=subject,
                    point_name=point_name,
                    mastery_score=base_score,
                    mastery_level=UserKnowledgeState.calc_mastery_level(base_score),
                    wrong_count=wrong,
                    correct_count=correct,
                    last_practice_time=datetime.now(),
                    update_time=datetime.now(),
                )
                self.db.add(record)

            updated_count += 1

        await self.db.flush()
        logger.info(
            "知识状态更新完成: user_id=%d, source=%s, updated=%d条",
            user_id, update_source, updated_count,
        )
        return updated_count

    async def query(
        self,
        user_id: int,
        subject: Optional[str] = None,
        time_range: Optional[str] = None,
        query_type: str = "掌握度汇总",
    ) -> dict:
        """
        查询用户知识状态

        Args:
            user_id: 用户ID
            subject: 学科筛选（不传则全学科）
            time_range: 时间范围（暂未实现，预留字段）
            query_type: 查询类型（薄弱点查询/掌握度汇总/进步点分析/学习建议）

        Returns:
            dict 包含 items, summary, weak_points, strong_points
        """
        # 构建查询条件
        conditions = [UserKnowledgeState.user_id == user_id]
        if subject:
            conditions.append(UserKnowledgeState.subject == subject)

        result = await self.db.execute(
            select(UserKnowledgeState)
            .where(and_(*conditions))
            .order_by(UserKnowledgeState.mastery_score.asc())
        )
        records = result.scalars().all()

        # 转换为列表
        items = [r.to_dict() for r in records]

        # 提取薄弱点和强项
        weak_points = [
            r.point_name for r in records
            if r.mastery_score <= 60
        ]
        strong_points = [
            r.point_name for r in records
            if r.mastery_score >= 85
        ]

        # 生成摘要
        if not records:
            summary = "暂无知识状态记录，完成作业分析后将自动生成。"
        elif query_type == "薄弱点查询":
            if weak_points:
                summary = f"共发现 {len(weak_points)} 个薄弱知识点：{'、'.join(weak_points[:5])}"
                if len(weak_points) > 5:
                    summary += f"等{len(weak_points)}项"
            else:
                summary = "未发现明显薄弱知识点，继续保持！"
        elif query_type == "进步点分析":
            strong_only = [r for r in records if r.mastery_score >= 85]
            if strong_only:
                summary = f"已掌握 {len(strong_only)} 个知识点：{'、'.join([r.point_name for r in strong_only[:5]])}"
            else:
                summary = "目前没有达到精通水平的知识点，继续加油！"
        elif query_type == "学习建议":
            if weak_points:
                summary = f"建议优先复习：{'、'.join(weak_points[:3])}。针对薄弱知识点，建议从基础概念入手，逐步提升。"
            else:
                summary = "当前学习状态良好，建议进行拓展练习，挑战更高难度的题目。"
        else:  # 掌握度汇总
            if records:
                avg_score = sum(r.mastery_score for r in records) / len(records)
                level_dist = {
                    "未掌握": len([r for r in records if r.mastery_score <= 30]),
                    "初步掌握": len([r for r in records if 31 <= r.mastery_score <= 60]),
                    "熟练掌握": len([r for r in records if 61 <= r.mastery_score <= 85]),
                    "精通": len([r for r in records if r.mastery_score >= 86]),
                }
                summary = (
                    f"共追踪 {len(records)} 个知识点，平均掌握度 {avg_score:.0f}分。"
                    f"未掌握:{level_dist['未掌握']}个, 初步掌握:{level_dist['初步掌握']}个, "
                    f"熟练掌握:{level_dist['熟练掌握']}个, 精通:{level_dist['精通']}个。"
                )
            else:
                summary = "暂无知识状态记录。"

        return {
            "items": items,
            "total": len(items),
            "summary": summary,
            "weak_points": weak_points,
            "strong_points": strong_points,
        }
