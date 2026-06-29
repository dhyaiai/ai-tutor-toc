"""学情分析 Schema 定义"""

from pydantic import BaseModel
from datetime import datetime


# ==================== 子板块1：作业统计 ====================

class SubjectStat(BaseModel):
    """各科目作业数量统计"""
    subject: str
    count: int


class HomeworkStatsResponse(BaseModel):
    """作业统计响应"""
    total: int
    subject_stats: list[SubjectStat]


# ==================== 子板块2：学生学期看板 ====================

class DashboardItem(BaseModel):
    """学生学期看板单条数据"""
    id: int
    name: str
    grade: str
    subject: str
    semester: str
    created_at: datetime
    score_rate: float  # 得分率，范围 0~1


class DashboardResponse(BaseModel):
    """学生学期看板响应"""
    items: list[DashboardItem]


# ==================== 子板块3：知识点热力图 ====================

class KnowledgeHeatmapItem(BaseModel):
    """知识点热力图单条数据"""
    knowledge_point: str
    frequency: int       # 考察频次
    score_rate: float    # 得分率，范围 0~1


class KnowledgeHeatmapResponse(BaseModel):
    """知识点热力图响应"""
    items: list[KnowledgeHeatmapItem]
