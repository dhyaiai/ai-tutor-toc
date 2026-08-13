from pydantic import BaseModel, Field
from typing import Literal


class ChatMessage(BaseModel):
    """对话历史消息（role 限制为 user/assistant，防止客户端注入 system role 覆盖系统提示词）"""
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=8000)


class ChatContext(BaseModel):
    grade: str | None = None
    subject: str | None = None


class ExplainRequest(BaseModel):
    """
    完整讲解直连请求体（不经过 Agent，直接调用 ExplainService.explain_full）

    - exercise_content: 题目上下文文本（题干/答案/解析拼接）
    - subject: 所属学科
    - explanation_style: 讲解风格（分步引导式/直接讲解式/基础科普式）
    - strict_level: 讲解严格度 1-5
    - question_id: 关联题目 ID（可选）。传入后后端读取该题的切割原图（含题干），
      以多模态方式喂给视觉模型，让 LLM 真正"看到"题目——
      纯文本上下文只含批改结果字段（正确答案/学生答案/解析），不含题干原文，
      没有图片时讲解是在"讲一道看不见的题"
    """
    exercise_content: str
    subject: str = "未知"
    explanation_style: str = "直接讲解式"
    strict_level: int = 3
    question_id: int | None = None


class ExplainCheckRequest(BaseModel):
    """
    思考题作答判题请求体

    - exercise_content: 原题上下文（供 LLM 自行解题后对比，参考答案不经过前端）
    - thinking_question: 讲解末尾生成的思考题
    - user_answer: 学生的回答
    - subject: 所属学科
    """
    exercise_content: str
    thinking_question: str
    user_answer: str
    subject: str = "未知"


class ChatRequest(BaseModel):
    """
    AI 助教聊天请求体

    - message: 用户当前输入的消息文本（上限 4000 字符，防止恶意超长输入）
    - history: 最近的对话历史（上限 20 条，防止上下文爆炸导致天价 token 账单）
    - context: 可选的年级/学科上下文信息
    - session_id: 可选的会话ID，用于关联消息到指定会话
                  传入后后端会在聊天过程中自动保存消息到对应会话
    """
    message: str = Field(..., max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
    context: ChatContext | None = None
    session_id: int | None = None


class FeedbackRequest(BaseModel):
    """
    讲解反馈记录请求体（直接更新知识状态，不经过 Agent 对话）

    - knowledge_point: 反馈的知识点名称
    - feedback_level: 听懂程度（完全听懂 / 部分听懂 / 没听懂）
    - question_id: 关联的题目 ID（可选）
    - session_id: 关联的会话 ID（可选）
    """
    knowledge_point: str
    feedback_level: str = "部分听懂"
    question_id: str | None = None
    session_id: str | None = None
