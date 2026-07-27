"""
知识状态追踪 Schema

定义知识状态更新、查询的请求/响应结构。
"""

from pydantic import BaseModel, Field


class KnowledgePointUpdate(BaseModel):
    """
    单个知识点更新项

    mastery_change 含义：
    +2: 作业正确 / 听懂讲解 / 订正正确 / 练习正确 / 口语正确 / 作文提升点
    +1: 练习正确（弱提升）
     0: 无变化（仅记录练习时间）
    -1: 作业错误 / 练习错误 / 口语错误 / 作文扣分点
    -2: 作业错误（严重）/ 订正后仍错
    """
    point_name: str = Field(..., description="知识点名称")
    subject: str = Field(default="通用", description="所属学科")
    mastery_change: int = Field(
        ..., ge=-2, le=2,
        description="掌握度变化：-2严重错误/-1错误/0不变/+1正确/+2优秀"
    )
    behavior_type: str = Field(
        default="练习正确",
        description="触发行为：作业正确/作业错误/听懂讲解/订正正确/练习正确/练习错误/口语正确/口语错误/作文提升点/作文扣分点"
    )


class KnowledgeStateUpdateRequest(BaseModel):
    """
    批量更新知识状态请求

    update_source 取值：
    作业分析/题目讲解/订正完成/练习测试/作文批改/口语测评
    """
    knowledge_points: list[KnowledgePointUpdate] = Field(
        ..., min_length=1, max_length=50,
        description="需要更新的知识点列表"
    )
    update_source: str = Field(
        ..., description="更新来源"
    )
    related_id: str | None = Field(
        default=None, description="关联的作业/题目/测评ID"
    )


class KnowledgeStateQueryRequest(BaseModel):
    """知识状态查询请求"""
    subject: str | None = Field(default=None, description="学科筛选")
    time_range: str | None = Field(default=None, description="时间范围")
    query_type: str = Field(
        default="掌握度汇总",
        description="查询类型：薄弱点查询/掌握度汇总/进步点分析/学习建议"
    )


class KnowledgeStateItem(BaseModel):
    """知识状态条目"""
    id: int
    point_name: str
    subject: str
    mastery_score: int
    mastery_level: str
    wrong_count: int
    correct_count: int
    last_practice_time: str | None = None
    update_time: str | None = None

    class Config:
        from_attributes = True


class KnowledgeStateResponse(BaseModel):
    """知识状态查询响应"""
    items: list[KnowledgeStateItem] = []
    total: int = 0
    summary: str = ""
    # 薄弱知识点列表（mastery_score <= 60）
    weak_points: list[str] = []
    # 掌握较好的知识点（mastery_score >= 85）
    strong_points: list[str] = []


class KnowledgeStateUpdateResponse(BaseModel):
    """知识状态更新响应"""
    updated_count: int = 0
    detail: str = ""
