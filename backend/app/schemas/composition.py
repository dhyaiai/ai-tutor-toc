"""作文批改 Schema"""

from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator


class CompositionCorrectRequest(BaseModel):
    """批改作文请求"""
    composition_content: str | None = Field(default=None, description="作文文本内容")
    composition_file_id: str | None = Field(default=None, description="作文图片文件ID")
    subject: str = Field(..., pattern="^(语文|英语)$", description="学科")
    grade: str | None = Field(default=None, description="年级")
    composition_title: str | None = Field(default=None, description="作文题目")
    requirement: str | None = Field(default=None, description="写作要求")
    strict_level: int = Field(default=3, ge=1, le=5, description="评分严格度")
    essay_type: str | None = Field(default=None, description="英语作文类型：读后续写/应用文/议论文等")


class RevisionSuggestion(BaseModel):
    """逐处修改建议"""
    position: str = ""
    original_text: str = ""
    revised_text: str = ""
    reason: str = ""
    revision_type: str = ""


class CompositionResponse(BaseModel):
    """批改结果响应"""
    id: int
    subject: str
    title: str
    total_score: int
    full_score: int
    content: str = ""
    grade: str | None = None
    dimension_scores: dict | None = None
    revision_suggestions: list[dict] | None = None
    overall_comment: str | None = None
    polish_advice: str | None = None
    sample_essay: str | None = None
    strict_level: int = 3
    essay_type: str | None = None
    pdf_url: str | None = None
    create_time: str | None = None

    model_config = {"from_attributes": True}

    @field_validator("create_time", mode="before")
    @classmethod
    def coerce_create_time(cls, v):
        """ORM 返回 datetime 对象，转为 ISO 字符串"""
        if isinstance(v, datetime):
            return v.isoformat()
        return v


class CompositionListItem(BaseModel):
    """批改记录列表项"""
    id: int
    subject: str
    title: str
    total_score: int
    full_score: int
    strict_level: int = 3
    grade: str | None = None
    essay_type: str | None = None
    pdf_url: str | None = None  # 原始上传文件路径，前端通过 /file-url 接口获取访问URL
    create_time: str | None = None

    model_config = {"from_attributes": True}

    @field_validator("create_time", mode="before")
    @classmethod
    def coerce_create_time(cls, v):
        """ORM 返回 datetime 对象，转为 ISO 字符串"""
        if isinstance(v, datetime):
            return v.isoformat()
        return v
