"""智能出题工作流的 Pydantic 数据模型。

这些模型同时承担两个职责：
1. 作为 LangChain 结构化输出（with_structured_output）的 response schema，
   约束 LLM 返回合法的 JSON；
2. 作为节点之间传递数据的强类型载体。
"""

from pydantic import BaseModel, Field


class Option(BaseModel):
    """选择题选项"""
    label: str = Field(description="选项标签，如 A/B/C/D")
    text: str = Field(description="选项内容")


class GeneratedQuestion(BaseModel):
    """改造阶段产出的单道题目（与 similar_generator.SimilarQuestion 字段对齐，便于复用落库逻辑）"""
    question_text: str = Field(description="（单选题）…/（多选题）…/（填空题）… 开头的题干")
    answer: str = Field(description="答案；多选题用逗号分隔正确选项，如 A,C,D")
    analysis: str = Field(description="完整解析：解题思路、步骤、依据")
    # 注意：knowledge_point / difficulty 改为带默认值而非必填。
    # 模型偶发漏输出时，缺字段会造成 model_validate 抛 ValidationError → 整轮重试，
    # 重试耗尽后返回空题列表 → 再回流 verify → 白白烧多轮 token。缺省后由
    # transform_node 用目标知识点/校准难度兜底，字段完整性由 prompt 模板保证。
    knowledge_point: str = Field(default="", description="考察的知识点")
    difficulty: str = Field(default="", description="easy | medium | hard")
    question_type: str = Field(description="单选题 / 多选题 / 填空题 / 解答题")
    options: list[Option] = Field(default_factory=list, description="选择题选项，非选择题为空")
    image_svg: str = Field(default="", description="题目配图纯 SVG 代码，无图时为空字符串")


class VerifyResult(BaseModel):
    """verify 节点（质量检查师）的判定结果"""
    passed: bool = Field(description="是否全部通过质量检查")
    issues: list[str] = Field(default_factory=list, description="发现的问题列表；通过时为空")
    suggestion: str = Field(default="", description="改进建议（fail 时作为反馈喂给 transform 重改）")


class GeneratedQuestions(BaseModel):
    """transform 节点一次改造产出的题目集合（供 with_structured_output 使用）"""
    questions: list[GeneratedQuestion] = Field(description="本次生成的题目列表")


class WebSearchResult(BaseModel):
    """search 节点（联网搜索师）的产出"""
    summary: str = Field(default="", description="搜索结果摘要")
    references: list[dict] = Field(default_factory=list, description="参考资料条目，每项含 title/source/content 等")
