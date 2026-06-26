"""
同类题生成服务。

基于原题的知识点、题型、难度，调用大模型生成同类变式题。
"""

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SimilarQuestion:
    question_text: str
    answer: str
    knowledge_point: str
    difficulty: str  # easy | medium | hard
    question_type: str = ""  # 题型
    options: list[dict] = field(default_factory=list)  # 选择题选项


NO_LATEX_RULE = (
    "【重要】所有内容使用试卷上的标准写法，严禁使用 LaTeX、MathJax、KaTeX 或任何数学排版语言。"
    "数学表达式用 Unicode 字符直接书写，就像试卷上印刷的那样。"
    "例如：x²+2x+1（不是 $x^2+2x+1$），√(a²+b²)，(a+b)/(c+d)，"
    "使用 × 表示乘号，÷ 表示除号，≤ ≥ ≠ ≈ 等数学符号。"
)

SIMILAR_PROMPT = """你是一位经验丰富的教师，需要根据原题生成 3 道同类变式题。

要求：
1. 题型必须与原题保持一致：{question_type}
2. 考察相同的知识点，但题目形式或数字不同
3. 难度分布：1道基础、1道中等、1道拔高
4. 每道题包含：题目、答案、难度标注
5. 如果是选择题，必须包含选项列表（options字段，每项含label和text）
6. 如果是多选题，题目文字开头必须标注"（多选题）"，且答案中用逗号分隔所有正确选项（如"A,C,D"）
7. 如果是单选题，题目文字开头必须标注"（单选题）"
8. 【重要】每道题必须返回 question_type 字段，准确标注题型——选择题必须区分"单选题"或"多选题"
9. {no_latex_rule}

原题信息：
- 题型：{question_type}
- 知识点：{knowledge_points}
- 学生答案：{student_answer}
- 正确答案：{correct_answer}
- 分析：{analysis_detail}

请返回 JSON：
{{
  "similar_questions": [
    {{
      "question_text": "（单选题）题目内容 或 （多选题）题目内容",
      "answer": "答案（多选则用逗号分隔，如A,C,D）",
      "knowledge_point": "知识点",
      "difficulty": "easy|medium|hard",
      "question_type": "单选题 或 多选题 或 填空题 或 解答题",
      "options": [{{"label": "A", "text": "选项内容"}}, ...]
    }}
  ]
}}"""

SINGLE_PROMPT = """你是一位经验丰富的教师，请根据原题生成 1 道同类变式题。

要求：
1. 题型必须与原题保持一致：{question_type}
2. 考察相同的知识点，但题目形式或数字不同
3. 难度为：{difficulty}
4. 如果是选择题，必须包含选项列表（options字段，每项含label和text）
5. 如果是多选题，题目文字开头必须标注"（多选题）"，且答案中用逗号分隔所有正确选项（如"A,C,D"）
6. 如果是单选题，题目文字开头必须标注"（单选题）"
7. 【重要】必须返回 question_type 字段，准确标注题型——选择题必须区分"单选题"或"多选题"
8. {no_latex_rule}
9. 不要生成与以下已有题目相似的题目：{exclude_text}

原题信息：
- 题型：{question_type}
- 知识点：{knowledge_points}
- 学生答案：{student_answer}
- 正确答案：{correct_answer}
- 分析：{analysis_detail}

请返回 JSON：
{{
  "question_text": "（单选题）题目内容 或 （多选题）题目内容",
  "answer": "答案（多选则用逗号分隔，如A,C,D）",
  "knowledge_point": "知识点",
  "difficulty": "easy|medium|hard",
  "question_type": "单选题 或 多选题 或 填空题 或 解答题",
  "options": [{{"label": "A", "text": "选项内容"}}, ...]
}}"""

GRADING_PROMPT = """你是一位教师，请对学生的作答进行评分。

题目：{question_text}
标准答案：{correct_answer}
学生作答：{user_answer}
知识点：{knowledge_point}
满分：{full_score}分

请返回 JSON：
{{
  "score": 得分（0到满分的整数或小数）,
  "full_score": 满分,
  "is_correct": true或false,
  "feedback": "简要评语（50字以内，指出对在哪里或错在哪里）"
}}"""


class SimilarGenerator:
    """同类题生成器"""

    def __init__(self):
        from app.core.config import get_settings
        settings = get_settings()
        self.llm_key = settings.LLM_API_KEY
        self.llm_base = settings.LLM_API_BASE
        self.model = settings.LLM_MODEL

    def _get_client(self):
        if not self.llm_key:
            return None
        from openai import AsyncOpenAI
        return AsyncOpenAI(api_key=self.llm_key, base_url=self.llm_base)

    @staticmethod
    def _extract_kp(knowledge_points: list[str] | None) -> str:
        return ", ".join(knowledge_points) if knowledge_points else "未知"

    @staticmethod
    def _parse_options(options_data: list | None) -> list[dict]:
        if not options_data or not isinstance(options_data, list):
            return []
        return [
            {"label": o.get("label", ""), "text": o.get("text", "")}
            for o in options_data
            if isinstance(o, dict)
        ]

    async def generate(
        self,
        knowledge_points: list[str] | None,
        student_answer: str | None,
        correct_answer: str | None,
        analysis_detail: str | None,
        question_type: str | None = None,
    ) -> list[SimilarQuestion]:
        """生成 3 道同类题。"""
        client = self._get_client()
        if not client:
            logger.warning("LLM API key not configured")
            return []

        kp_str = self._extract_kp(knowledge_points)
        qt_str = question_type or "未知"
        prompt = SIMILAR_PROMPT.format(
            question_type=qt_str,
            knowledge_points=kp_str,
            student_answer=student_answer or "未作答",
            correct_answer=correct_answer or "未知",
            analysis_detail=analysis_detail or "无",
            no_latex_rule=NO_LATEX_RULE,
        )

        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0.6,
                response_format={"type": "json_object"},
                timeout=120,
            )
            data = json.loads(response.choices[0].message.content or "{}")
            questions = data.get("similar_questions", [])
            result = []
            for q in questions[:3]:
                qt = q.get("question_text", "").strip()
                if not qt:
                    continue  # skip empty results
                result.append(SimilarQuestion(
                    question_text=qt,
                    answer=q.get("answer", "").strip(),
                    knowledge_point=q.get("knowledge_point", kp_str),
                    difficulty=q.get("difficulty", "medium"),
                    question_type=q.get("question_type") or qt_str,
                    options=self._parse_options(q.get("options")),
                ))
            return result
        except Exception as e:
            logger.error("Similar question generation failed: %s", e)
            return []

    async def generate_one(
        self,
        knowledge_points: list[str] | None,
        student_answer: str | None,
        correct_answer: str | None,
        analysis_detail: str | None,
        question_type: str | None = None,
        difficulty: str = "medium",
        exclude_text: str = "",
    ) -> SimilarQuestion | None:
        """生成 1 道同类题（用于换一题）"""
        client = self._get_client()
        if not client:
            return None

        kp_str = self._extract_kp(knowledge_points)
        qt_str = question_type or "未知"
        prompt = SINGLE_PROMPT.format(
            question_type=qt_str,
            knowledge_points=kp_str,
            student_answer=student_answer or "未作答",
            correct_answer=correct_answer or "未知",
            analysis_detail=analysis_detail or "无",
            difficulty=difficulty,
            exclude_text=exclude_text or "无",
            no_latex_rule=NO_LATEX_RULE,
        )

        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.6,
                response_format={"type": "json_object"},
                timeout=120,
            )
            data = json.loads(response.choices[0].message.content or "{}")
            question_text = data.get("question_text", "").strip()
            answer = data.get("answer", "").strip()
            if not question_text:
                logger.warning("Generated question has empty question_text, treating as failure")
                return None
            return SimilarQuestion(
                question_text=question_text,
                answer=answer,
                knowledge_point=data.get("knowledge_point", kp_str),
                difficulty=data.get("difficulty", difficulty),
                question_type=data.get("question_type") or qt_str,
                options=self._parse_options(data.get("options")),
            )
        except Exception as e:
            logger.error("Single similar question generation failed: %s", e)
            return None

    async def grade_answer(
        self,
        question_text: str,
        correct_answer: str,
        user_answer: str,
        knowledge_point: str,
        full_score: float = 100.0,
    ) -> dict:
        """AI 评分用户作答"""
        client = self._get_client()
        if not client:
            return {"score": 0, "full_score": full_score, "is_correct": False, "feedback": "评分服务未配置"}

        prompt = GRADING_PROMPT.format(
            question_text=question_text,
            correct_answer=correct_answer,
            user_answer=user_answer,
            knowledge_point=knowledge_point,
            full_score=full_score,
        )

        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.3,
                response_format={"type": "json_object"},
                timeout=60,
            )
            data = json.loads(response.choices[0].message.content or "{}")
            return {
                "score": data.get("score", 0),
                "full_score": data.get("full_score", full_score),
                "is_correct": data.get("is_correct", False),
                "feedback": data.get("feedback", ""),
            }
        except Exception as e:
            logger.error("Grading failed: %s", e)
            return {"score": 0, "full_score": full_score, "is_correct": False, "feedback": f"评分失败: {e}"}
