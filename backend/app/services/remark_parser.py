"""
教师备注解析器 —— 从人工审核备注中提取强制覆盖指令。

教师通过"重新生成"按钮输入的备注属于人工审核结果，
需要作为权威依据强制覆盖 AI 的识别结果，而非仅仅作为 prompt 提示。
"""

import re
import logging

logger = logging.getLogger(__name__)


def parse_remark_overrides(remark: str | None) -> dict:
    """
    从教师备注中解析出明确的字段覆盖值。

    支持的表达模式：
    - 学生答案："学生选的是D" / "学生答案是D" / "学生写的D"
    - 正确答案："正确答案是B" / "答案B"
    - 得分："得5分" / "得分5分"
    - 满分："满分10分"

    Returns:
        dict: 如 {"student_answer": "D"}，无匹配时返回 {}
    """
    if not remark or not remark.strip():
        return {}

    overrides = {}
    text = remark.strip()

    # ── 1. 学生答案模式（最高优先级，教师人工确认过学生的实际作答）──
    # 匹配："学生选的是D"、"学生答案是D"、"学生写了D"、"学生选择C"等
    student_patterns = [
        # 通用："学生/该生/孩子 + 选/答/写/做 + 的是/了/为/：/ + 答案值"
        r'(?:学生|该生|孩子|考生).{0,10}?(?:选[的了择]?|答[了案]?|写[的了]?|做[的了]?|填[的了]?).{0,6}?(?:是|为|：|:|,|，)\s*([A-Za-z0-9]+)',
        # 简写："选D"、"答D"（紧跟在学生后面）
        r'学生.{0,4}?[选答写做填].{0,2}?([A-Za-z0-9]+)',
        # "学生答案D"、"学生作答D"
        r'学生(?:答案|作答|回答)[是为：:,\s]*([A-Za-z0-9]+)',
    ]

    for pattern in student_patterns:
        match = re.search(pattern, text)
        if match:
            val = match.group(1).strip().rstrip('.。,，;；')
            # 只接受合理的答案值（1-20个字符）
            if 1 <= len(val) <= 20:
                overrides['student_answer'] = val
                logger.info("备注解析: student_answer 覆盖 = '%s'（匹配模式: %s）", val, pattern[:40])
                break

    # ── 2. 正确答案模式 ──
    # 匹配："正确答案是B"、"答案是C"、"此题答案B"
    correct_patterns = [
        r'正确\s*答案\s*[是为：:,\s]*\s*([A-Za-z0-9]+)',
        r'(?:参考|标准)\s*答案\s*[是为：:,\s]*\s*([A-Za-z0-9]+)',
        r'[此题]答案\s*[应是为：:,\s]*\s*([A-Za-z0-9]+)',
    ]

    for pattern in correct_patterns:
        match = re.search(pattern, text)
        if match:
            val = match.group(1).strip().rstrip('.。,，;；')
            if 1 <= len(val) <= 20:
                overrides['correct_answer'] = val
                logger.info("备注解析: correct_answer 覆盖 = '%s'", val)
                break

    # ── 3. 得分模式 ──
    # 匹配："得5分"、"打5分"、"给5分"、"得分5分"、"判5分"
    score_match = re.search(r'(?:得|打|给|评|得分|判)[\s]*(\d+(?:\.\d+)?)\s*分', text)
    if score_match:
        try:
            val = float(score_match.group(1))
            if 0 <= val <= 1000:
                overrides['score'] = val
                logger.info("备注解析: score 覆盖 = %.1f", val)
        except ValueError:
            pass

    # ── 4. 满分模式 ──
    # 匹配："满分10分"、"满分10"、"本题满分10分"
    full_score_match = re.search(r'满分\s*[是为：:,\s]*\s*(\d+(?:\.\d+)?)\s*分?', text)
    if full_score_match:
        try:
            val = float(full_score_match.group(1))
            if 0 < val <= 1000:
                overrides['full_score'] = val
                logger.info("备注解析: full_score 覆盖 = %.1f", val)
        except ValueError:
            pass

    return overrides


def apply_remark_overrides(grade_result, overrides: dict) -> int:
    """
    将解析出的覆盖值应用到 GradeResult（或 SubGradeResult）。

    直接修改 grade_result 对象的属性，并设置 confidence = 1.0。

    Args:
        grade_result: GradeResult 或 SubGradeResult 对象
        overrides: parse_remark_overrides 返回的字典

    Returns:
        int: 实际应用的覆盖字段数量
    """
    if not overrides:
        return 0

    applied = 0
    if 'student_answer' in overrides:
        grade_result.student_answer = overrides['student_answer']
        applied += 1
    if 'correct_answer' in overrides:
        grade_result.correct_answer = overrides['correct_answer']
        applied += 1
    if 'score' in overrides:
        grade_result.score = overrides['score']
        applied += 1
    if 'full_score' in overrides:
        grade_result.full_score = overrides['full_score']
        applied += 1

    if applied > 0:
        # 教师已人工确认，置信度设为最高
        grade_result.confidence = 1.0
        logger.info("备注覆盖: 共应用 %d 个字段，置信度设为 1.0", applied)

    return applied
