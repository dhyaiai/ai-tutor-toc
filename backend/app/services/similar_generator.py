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
    options: list[dict] = field(default_factory=list)  # 单选题/多选题选项
    full_score: float = 0.0  # 分值
    analysis: str = ""  # 完整解析（解题思路、步骤、依据）


@dataclass
class SimilarBigQuestion:
    """类似大题（含多个子题）"""
    question_context: str  # 大题背景材料
    sub_questions: list[SimilarQuestion] = field(default_factory=list)


NO_LATEX_RULE = (
    "【重要】所有内容使用试卷上的标准写法，严禁使用 LaTeX、MathJax、KaTeX 或任何数学排版语言。"
    "数学表达式用 Unicode 字符直接书写，就像试卷上印刷的那样。"
    "例如：x²+2x+1（不是 $x^2+2x+1$），√(a²+b²)，(a+b)/(c+d)，"
    "使用 × 表示乘号，÷ 表示除号，≤ ≥ ≠ ≈ 等数学符号。"
)

ANTI_LEAK_RULE = (
    "【严禁泄露答案】题干中绝对不能出现、写明或暗示正确答案。"
    "对于文言文断句题，题干中给出的画线句子必须是【完全未断句的连续原文】，"
    "不得添加任何“/”、空格、逗号或其它停顿/断句标记；正确的断句方式只能作为其中一个选项出现，"
    "绝不能直接呈现在题干里。对于其它选择题，题干同样不得给出或提示正确选项。"
)

SIMILAR_PROMPT = """你是一位经验丰富的教师，需要根据原题生成 3 道同类变式题。

要求：
1. 题型必须与原题保持一致：{question_type}
2. 考察相同的知识点，但题目形式或数字不同
3. 难度分布：1道基础、1道中等、1道拔高
4. 每道题包含：题目、答案、【完整解析】、难度标注
5. 如果是单选题或多选题，必须包含选项列表（options字段，每项含label和text）
6. 如果是多选题，题目文字开头必须标注"（多选题）"，且答案中用逗号分隔所有正确选项（如"A,C,D"）
7. 如果是单选题，题目文字开头必须标注"（单选题）"
8. 【重要】每道题必须返回 question_type 字段，准确标注题型——必须使用"单选题"或"多选题"，不要使用笼统的"选择题"
9. 【解析必须完整】每道题必须返回 analysis 字段，给出完整的解题解析：包括解题思路、关键步骤、所用知识点与依据、以及为何该答案正确（选择题需说明为何其它选项错误），不少于两句话，不能只写答案
10. {no_latex_rule}
11. {anti_leak_rule}
12. 生成的题目参考学科网近期上传的同类型题目并结合最新的中、高考大纲，以提高题目的质量

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
      "analysis": "完整解析：解题思路、步骤、依据，以及答案正确的原因",
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
4. 如果是单选题或多选题，必须包含选项列表（options字段，每项含label和text）
5. 如果是多选题，题目文字开头必须标注"（多选题）"，且答案中用逗号分隔所有正确选项（如"A,C,D"）
6. 如果是单选题，题目文字开头必须标注"（单选题）"
7. 【重要】必须返回 question_type 字段，准确标注题型——必须使用"单选题"或"多选题"，不要使用笼统的"选择题"
8. 【解析必须完整】必须返回 analysis 字段，给出完整的解题解析：包括解题思路、关键步骤、所用知识点与依据、以及为何该答案正确（选择题需说明为何其它选项错误），不少于两句话，不能只写答案
9. {no_latex_rule}
10. {anti_leak_rule}
11. 不要生成与以下已有题目相似的题目：{exclude_text}
12. 生成的题目参考学科网近期上传的同类型题目并结合最新的中、高考大纲，以提高题目的质量

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
  "analysis": "完整解析：解题思路、步骤、依据，以及答案正确的原因",
  "knowledge_point": "知识点",
  "difficulty": "easy|medium|hard",
  "question_type": "单选题 或 多选题 或 填空题 或 解答题",
  "options": [{{"label": "A", "text": "选项内容"}}, ...]
}}"""

BIG_QUESTION_PROMPT = """你是一位经验丰富的教师，需要根据原大题（含多个小题）生成 1 道完整的类似大题。

要求：
1. 保持与原文相同的大题结构：大题背景材料 + 相同数量的小题
2. 每个小题的题型必须与原题对应的小题保持一致
3. 考察相同的知识点体系，但具体内容、材料、数据不同
4. 难度为：{difficulty}
5. 单选题或多选题必须包含选项列表（options字段，每项含label和text）
6. 如果是多选题，题目文字开头必须标注"（多选题）"，且答案中用逗号分隔所有正确选项
7. 如果是单选题，题目文字开头必须标注"（单选题）"
8. 填空题/简答题/解答题等主观题，题目文字开头必须标注题型，如"（简答题）"
9. 【重要】所有内容使用试卷上的标准写法，严禁使用 LaTeX、MathJax、KaTeX 或任何数学排版语言
10. 每个子题必须返回 question_type 字段，准确标注题型
11. 【分值必须精确】每个子题的 full_score 必须等于下面"各小题详情"中标注的对应分值，不得自行编造
12. 【解析必须完整】每个子题必须返回 analysis 字段，给出完整的解题解析：包括解题思路、关键步骤、所用知识点与依据、以及为何该答案正确，不少于两句话，不能只写答案
13. {anti_leak_rule}

原大题信息：
- 大题题号：第{question_number}题
- 大题题型：{question_type}
- 子题数量：{sub_count} 题
- 目标难度：{difficulty}

各小题详情（含精确分值，生成时必须使用这些分值）：
{sub_questions_detail}

请返回 JSON：
{{
  "question_context": "大题背景材料（如阅读文章、实验描述等；如果原大题没有背景材料则填空字符串）",
  "sub_questions": [
    {{
      "question_text": "（单选题）... 或 （多选题）... 或 （简答题）...",
      "answer": "答案（多选则用逗号分隔，如A,C,D）",
      "analysis": "完整解析：解题思路、步骤、依据，以及答案正确的原因",
      "knowledge_point": "知识点",
      "difficulty": "{difficulty}",
      "question_type": "单选题 或 多选题 或 填空题 或 简答题 等",
      "options": [{{"label": "A", "text": "选项内容"}}, ...],
      "full_score": 必须使用下方对应小题的精确分值
    }}
  ]
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
            anti_leak_rule=ANTI_LEAK_RULE,
        )

        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1600,
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
                    analysis=(q.get("analysis") or "").strip(),
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
            anti_leak_rule=ANTI_LEAK_RULE,
        )

        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=900,
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
                analysis=(data.get("analysis") or "").strip(),
            )
        except Exception as e:
            logger.error("Single similar question generation failed: %s", e)
            return None

    async def generate_similar_big_question(
        self,
        parent_question: dict,
        children: list[dict],
        difficulty: str = "medium",
    ) -> SimilarBigQuestion | None:
        """
        根据父题信息和所有子题数据，生成 1 道类似的完整大题。

        Args:
            parent_question: 父题信息，含 question_number, question_type, knowledge_points 等
            children: 子题列表，每项含 question_type, student_answer, correct_answer,
                      knowledge_points, analysis_detail, score, full_score 等
            difficulty: 难度（easy/medium/hard），默认 medium

        Returns:
            SimilarBigQuestion 或 None（生成失败时）
        """
        client = self._get_client()
        if not client:
            logger.warning("LLM API key not configured")
            return None

        # 收集原始子题分值，用于生成后强制覆盖
        original_scores: list[float] = []
        # 构建子题详情文本
        sub_lines = []
        for i, child in enumerate(children):
            qt = child.get("question_type") or "未知"
            kp = child.get("knowledge_points")
            if isinstance(kp, list):
                kp_str = ", ".join(
                    k["name"] if isinstance(k, dict) else str(k)
                    for k in kp
                )
            elif isinstance(kp, dict):
                kp_str = ", ".join(str(v) for v in kp.values())
            else:
                kp_str = str(kp) if kp else "未知"
            fs = child.get("full_score") or 0
            original_scores.append(float(fs))
            sub_lines.append(
                f"  小题{i+1}：题型={qt}，知识点={kp_str}，【分值必须={fs}分，不可更改】"
            )

        prompt = BIG_QUESTION_PROMPT.format(
            question_number=parent_question.get("question_number", "?"),
            question_type=parent_question.get("question_type") or "未知",
            sub_count=len(children),
            difficulty=difficulty,
            sub_questions_detail="\n".join(sub_lines),
            anti_leak_rule=ANTI_LEAK_RULE,
        )

        try:
            response = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=3200,
                temperature=0.6,
                response_format={"type": "json_object"},
                timeout=180,
            )
            data = json.loads(response.choices[0].message.content or "{}")
            question_context = data.get("question_context", "").strip()
            sub_list = data.get("sub_questions", [])

            if not sub_list:
                logger.warning("Generated big question has no sub_questions")
                return None

            result_sub_questions = []
            for idx, sq in enumerate(sub_list[:len(children)]):  # 不超过原子题数量
                qt = sq.get("question_text", "").strip()
                if not qt:
                    continue
                # 强制使用原始分值，忽略AI可能编造的分值
                forced_score = original_scores[idx] if idx < len(original_scores) else float(sq.get("full_score", 0))
                result_sub_questions.append(SimilarQuestion(
                    question_text=qt,
                    answer=sq.get("answer", "").strip(),
                    knowledge_point=sq.get("knowledge_point", ""),
                    difficulty=difficulty,  # 使用外部传入的难度
                    question_type=sq.get("question_type") or "",
                    options=self._parse_options(sq.get("options")),
                    full_score=forced_score,
                    analysis=(sq.get("analysis") or "").strip(),
                ))

            if not result_sub_questions:
                return None

            return SimilarBigQuestion(
                question_context=question_context,
                sub_questions=result_sub_questions,
            )
        except Exception as e:
            logger.error("Similar big question generation failed: %s", e)
            return None

    async def grade_answer(
        self,
        question_text: str,
        correct_answer: str,
        user_answer: str,
        knowledge_point: str,
        full_score: float = 100.0,
        personality_directive: str | None = None,
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
        if personality_directive:
            # 用户自定义微调：性格/说话风格/评分严格度对所有批改生效
            prompt = f"{personality_directive}\n\n{prompt}"

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
