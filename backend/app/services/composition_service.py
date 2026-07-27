"""
作文智能批改服务

调用 LLM 对语文/英语作文进行结构化批改：
- 语文（60分制）：立意(20%)、结构(20%)、内容(30%)、语言(30%)
- 英语（25分制）：内容(8分)、语言(12分)、规范(5分)
- 输出逐处修改建议、整体评价、润色方向、参考范文

支持两种模式：
- 文本模式：传入作文文本，直接调用 LLM
- 多模态模式：传入图片 base64 列表，调用视觉 LLM 先识别文字再批改
"""

import json
import logging
import re
from typing import Optional
from openai import AsyncOpenAI
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 作文批改 max_tokens 配置
# 文本模式：范文+评价+建议 ≈ 2000-3000 tokens，留余量到 4096
TEXT_MAX_TOKENS = 4096
# 多模态模式：需额外输出识别全文（content 字段），需更大余量
MULTIMODAL_MAX_TOKENS = 8192


def _safe_parse_json(text: str, fallback: dict | None = None) -> dict:
    """
    安全解析 LLM 返回的 JSON，处理截断/格式异常。

    常见问题：
    1. max_tokens 不足导致 JSON 在字符串中间截断
    2. LLM 在 JSON 前后附加了说明文字
    3. 字符串中未转义的特殊字符

    修复策略（按顺序尝试）：
    1. 直接 json.loads
    2. 提取 ```json ... ``` 代码块再解析
    3. 提取第一个 { 到最后一个 } 之间的内容
    4. 补全截断的字符串和括号后解析
    5. 逐步截断前缀，找到最长可解析的子串
    """
    if not text or not text.strip():
        return fallback or {}

    raw = text.strip()

    # 策略1：直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 策略2：提取 ```json ... ``` 代码块
    code_block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 策略3：提取第一个 { 到最后一个 } 之间的内容（处理 LLM 在 JSON 外附加文字）
    first_brace = raw.find('{')
    last_brace = raw.rfind('}')
    if first_brace != -1 and last_brace > first_brace:
        extracted = raw[first_brace:last_brace + 1]
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            pass

    # 策略4：补全截断的 JSON（关闭未闭合的字符串和括号）
    repaired = _repair_truncated_json(raw)
    if repaired:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # 策略5：逐步截断，找到最长可解析前缀
    for i in range(len(raw) - 1, 0, -1):
        candidate = _repair_truncated_json(raw[:i])
        if candidate:
            try:
                result = json.loads(candidate)
                logger.warning("JSON 截断修复成功，丢失了末尾 %d 个字符", len(raw) - i)
                return result
            except json.JSONDecodeError:
                continue

    logger.error("JSON 解析完全失败，原始内容前500字符: %s", raw[:500])
    if fallback is not None:
        return fallback
    raise ValueError(f"无法解析 LLM 返回的 JSON: {raw[:200]}...")


def _repair_truncated_json(text: str) -> str | None:
    """
    尝试修复被截断的 JSON。
    处理：未闭合的字符串、未闭合的对象/数组、尾部多余逗号。
    """
    if not text:
        return None

    text = text.strip()

    # 跟踪状态：是否在字符串内、括号栈
    in_string = False
    escape_next = False
    brace_stack: list[str] = []
    last_complete_pos = 0  # 最后一个完整结构的位置

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            last_complete_pos = i + 1
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            if not in_string:
                last_complete_pos = i + 1
            continue
        if in_string:
            continue
        if ch in '{[':
            brace_stack.append(ch)
        elif ch == '}':
            if brace_stack and brace_stack[-1] == '{':
                brace_stack.pop()
                last_complete_pos = i + 1
        elif ch == ']':
            if brace_stack and brace_stack[-1] == '[':
                brace_stack.pop()
                last_complete_pos = i + 1

    # 从 last_complete_pos 截断
    repaired = text[:last_complete_pos] if last_complete_pos > 0 else text

    # 如果在字符串中间，先关闭字符串
    if in_string:
        repaired += '"'

    # 去掉尾部多余的逗号
    repaired = repaired.rstrip().rstrip(',')

    # 如果根本没有 { 开头，无法修复
    if not repaired.strip().startswith('{'):
        return None

    # 重新统计括号（因为截断点可能在结构内部）
    open_braces = repaired.count('{') - repaired.count('}')
    open_brackets = repaired.count('[') - repaired.count(']')

    # 关闭所有未闭合的结构
    repaired += '}' * max(0, open_braces)
    repaired += ']' * max(0, open_brackets)

    return repaired

CORRECTION_SYSTEM_PROMPT = """你是资深的作文批改老师，严格按照对应学科的评分标准与当前严格等级进行批改。

【通用规则】
1. 评分符合对应年级考试评分标准，严格等级越高，打分越严，扣分点越多
2. 修改建议必须具体到字/词/句，给出「原文-修改后-修改理由」三段式结构
3. 修改类型分类：错别字、语病、用词不当、句式优化、文采提升、结构调整
4. 禁止笼统评价，所有建议必须可落地、可直接替换使用
5. 参考范文符合题目要求，水平略高于学生当前水平，**范文字数必须严格符合该年级/学科的作文字数要求**（见下方字数标准）

【字数标准 - ，作文题目有字数要求的，范文需优先和题目字数要求保持一致，标点符号不算字数，参考范文必须遵守】
语文作文（按年级）：
- 小学一年级~二年级：100~200字
- 小学三年级~四年级：300~400字
- 小学五年级~六年级：400~500字
- 初中七年级~九年级：600~700字
- 高中：800~1000字

英语作文（按年级）：
- 小学：50~80词
- 初中：80~120词
- 高中：120~200词

【重要：忽略文件上的已有批改痕迹】
上传的作文文件可能已被老师或学生标记过分数、写过错别字纠正、旁批、总评等批改痕迹。**严禁识别或参考这些已有标记**——你必须仅根据作文原文内容本身进行独立评分和批改，忽略文件上的任何非原文字迹（红笔打分、圈画、旁注、评语等）。

【分值识别 - 最高优先级】
仔细查看上传的作文题目图片/文字中是否有明确的满分分值标注（如"本题满分40分"、"满分：25分"、"共60分"等）。**这些是题目原始设定的分值，必须优先使用**。如果识别到明确的分值，将其填入 recognized_full_score 字段；如果未在题目中找到任何分值标注，recognized_full_score 设为 null。注意区分题目给的分值和老师批改的打分——前者是"本题满分XX分"这种印刷字体，后者是手写的红色分数。只识别印刷的分值标注，忽略任何手写批改痕迹。

【语文作文批改维度】
- 立意（20%）：中心明确度、思想深度、扣题度
- 结构（20%）：篇章结构、段落逻辑、过渡衔接
- 内容（30%）：素材丰富度、论据充分性、情感真实性
- 语言（30%）：表达流畅度、文采、字词标点错误

【英语作文批改维度】
- 内容（8分）：扣题度、要点完整性、逻辑连贯
- 语言（12分）：语法准确性、词汇丰富度、句式多样性
- 规范（5分）：拼写、标点、格式

【输出格式（JSON）】
{
  "recognized_full_score": null,
  "total_score": 42,
  "dimension_scores": {"立意": 12, "结构": 10, "内容": 11, "语言": 9},
  "overall_comment": "整体评价...",
  "revision_suggestions": [
    {"position": "第2段第3句", "original_text": "原文", "revised_text": "修改后", "reason": "理由", "revision_type": "语言润色"}
  ],
  "polish_advice": "整体润色方向...",
  "sample_essay": "参考范文全文..."
}
"""


def _get_default_full_score(subject: str, essay_type: str | None = None) -> int:
    """
    根据学科和作文类型计算默认满分。

    规则：
    - 语文：60分
    - 英语 + 读后续写：25分
    - 英语 + 其他类型（应用文/议论文/概要写作等）：15分
    - 英语 + 未指定类型：15分
    """
    if subject == "语文":
        return 60
    # 英语
    if essay_type and "读后续写" in essay_type:
        return 25
    return 15


def _normalize_total_score(total_score, dimension_scores):
    """
    用维度评分之和修正总分，确保 total_score 与各维度分数一致。

    LLM 有时会返回不一致的 total_score 和 dimension_scores，
    这里以维度评分为准——如果维度评分存在且非空，用其总和覆盖总分。
    """
    if dimension_scores and isinstance(dimension_scores, dict) and len(dimension_scores) > 0:
        dim_sum = sum(v for v in dimension_scores.values() if isinstance(v, (int, float)))
        if dim_sum > 0:
            return dim_sum
    return total_score


class CompositionService:
    """作文批改服务"""

    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )
        self.model = settings.LLM_MODEL

    async def correct(
        self,
        content: str,
        subject: str,
        grade: Optional[str] = None,
        title: Optional[str] = None,
        requirement: Optional[str] = None,
        strict_level: int = 3,
        essay_type: Optional[str] = None,
        personality_directive: Optional[str] = None,
    ) -> dict:
        """
        调用 LLM 批改作文（文本模式），返回结构化批改结果

        Args:
            content: 作文全文
            subject: 学科（语文/英语）
            grade: 年级
            title: 作文题目
            requirement: 写作要求
            strict_level: 评分严格度 1-5
            essay_type: 作文类型（读后续写/应用文/议论文等），用于确定默认满分
            personality_directive: 助教个性化批改指令（性格/说话风格/评分严格度）
        """
        default_full_score = _get_default_full_score(subject, essay_type)

        # 构建 prompt
        if subject == "语文":
            subject_note = f"满分{default_full_score}分，按立意/结构/内容/语言四维度评分。"
        else:
            subject_note = f"满分{default_full_score}分，按内容/语言/规范三维度评分。"

        user_prompt = (
            f"学科：{subject}\n"
            f"作文类型：{essay_type or '未指定'}\n"
            f"默认满分：{default_full_score}分\n"
            f"评分维度：{subject_note}\n"
            f"严格等级：{strict_level}/5\n"
            f"年级：{grade or '未指定'}\n"
            f"作文题目：{title or '未指定'}\n"
            f"写作要求：{requirement or '无特殊要求'}\n"
            f"\n作文全文：\n{content}\n"
            f"\n请按 JSON 格式输出批改结果。"
        )
        if personality_directive:
            # 用户自定义微调：性格/说话风格/评分严格度对所有批改生效
            user_prompt = f"{personality_directive}\n\n{user_prompt}"

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CORRECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=TEXT_MAX_TOKENS,
                temperature=0.3,
                response_format={"type": "json_object"},
                timeout=120,
            )

            raw = response.choices[0].message.content or "{}"
            # 首次解析不用 fallback——解析失败说明 JSON 损坏严重，触发重试
            data = _safe_parse_json(raw)
            # 优先使用 LLM 从文件中识别到的分值，否则用默认分值
            recognized = data.get("recognized_full_score")
            if isinstance(recognized, (int, float)) and recognized > 0:
                full_score = int(recognized)
            else:
                full_score = default_full_score
            return {
                "total_score": _normalize_total_score(data.get("total_score", 0), data.get("dimension_scores")),
                "full_score": full_score,
                "dimension_scores": data.get("dimension_scores", {}),
                "overall_comment": data.get("overall_comment", ""),
                "revision_suggestions": data.get("revision_suggestions", []),
                "polish_advice": data.get("polish_advice", ""),
                "sample_essay": data.get("sample_essay", ""),
            }

        except (json.JSONDecodeError, ValueError) as e:
            # JSON 解析失败——可能是 max_tokens 不足导致截断
            logger.warning("作文批改JSON解析失败，尝试用更大max_tokens重试: %s", e)
            return await self._correct_with_retry(
                user_prompt=user_prompt,
                default_full_score=default_full_score,
                retry_max_tokens=TEXT_MAX_TOKENS * 2,
            )
        except Exception as e:
            logger.error("作文批改失败: %s", e)
            raise

    async def _correct_with_retry(
        self,
        user_prompt: str,
        default_full_score: int,
        retry_max_tokens: int,
    ) -> dict:
        """max_tokens 翻倍重试，解决 JSON 截断问题"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CORRECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=retry_max_tokens,
                temperature=0.3,
                response_format={"type": "json_object"},
                timeout=180,
            )
            raw = response.choices[0].message.content or "{}"
            # 重试时用 fallback——至少返回部分可用数据
            data = _safe_parse_json(raw, fallback={})
            # 优先使用 LLM 从文件中识别到的分值，否则用默认分值
            recognized = data.get("recognized_full_score")
            full_score = int(recognized) if isinstance(recognized, (int, float)) and recognized > 0 else default_full_score
            return {
                "total_score": _normalize_total_score(data.get("total_score", 0), data.get("dimension_scores")),
                "full_score": full_score,
                "dimension_scores": data.get("dimension_scores", {}),
                "overall_comment": data.get("overall_comment", ""),
                "revision_suggestions": data.get("revision_suggestions", []),
                "polish_advice": data.get("polish_advice", ""),
                "sample_essay": data.get("sample_essay", ""),
            }
        except Exception as e:
            logger.error("作文批改重试仍失败: %s", e)
            raise

    async def correct_multimodal(
        self,
        images: list[str],
        subject: str,
        grade: Optional[str] = None,
        title: Optional[str] = None,
        requirement: Optional[str] = None,
        strict_level: int = 3,
        essay_type: Optional[str] = None,
        personality_directive: Optional[str] = None,
    ) -> dict:
        """
        调用多模态 LLM 识别图片中的作文文字并批改。
        复用项目配置的 LLM_MODEL（qwen3.7-plus 支持视觉）。

        Args:
            images: base64 图片 data URL 列表（如 "data:image/png;base64,..."），
                    多页 PDF 每页一张图
            subject: 学科（语文/英语）
            grade: 年级
            title: 作文题目
            requirement: 写作要求
            strict_level: 评分严格度 1-5
            essay_type: 作文类型（读后续写/应用文/议论文等），用于确定默认满分
            personality_directive: 助教个性化批改指令（性格/说话风格/评分严格度）
        """
        default_full_score = _get_default_full_score(subject, essay_type)

        if subject == "语文":
            subject_note = f"满分{default_full_score}分，按立意/结构/内容/语言四维度评分。"
        else:
            subject_note = f"满分{default_full_score}分，按内容/语言/规范三维度评分。"

        # 文本提示部分
        text_prompt = (
            f"学科：{subject}\n"
            f"作文类型：{essay_type or '未指定'}\n"
            f"默认满分：{default_full_score}分\n"
            f"评分维度：{subject_note}\n"
            f"严格等级：{strict_level}/5\n"
            f"年级：{grade or '未指定'}\n"
            f"作文题目：{title or '未指定'}\n"
            f"写作要求：{requirement or '无特殊要求'}\n"
            f"\n请先识别图片中的作文文字，再按 JSON 格式输出批改结果。"
            f"JSON 中必须包含 content 字段，值为识别出的作文全文。"
        )
        if personality_directive:
            # 用户自定义微调：性格/说话风格/评分严格度对所有批改生效
            text_prompt = f"{personality_directive}\n\n{text_prompt}"

        # 构建多模态消息内容：[图片1, 图片2, ..., 文本提示]
        content_parts = []
        for img in images:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": img, "detail": "high"},
            })
        content_parts.append({"type": "text", "text": text_prompt})

        try:
            response = await self.client.chat.completions.create(
                model=self.model,  # 复用项目 LLM_MODEL（qwen3.7-plus 支持视觉）
                messages=[
                    {"role": "system", "content": CORRECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": content_parts},
                ],
                max_tokens=MULTIMODAL_MAX_TOKENS,
                temperature=0.3,
                response_format={"type": "json_object"},
                timeout=180,
            )

            raw = response.choices[0].message.content or "{}"
            # 首次解析不用 fallback——解析失败说明 JSON 损坏严重，触发重试
            data = _safe_parse_json(raw)
            # 优先使用 LLM 从文件中识别到的分值，否则用默认分值
            recognized = data.get("recognized_full_score")
            full_score = int(recognized) if isinstance(recognized, (int, float)) and recognized > 0 else default_full_score
            return {
                "total_score": _normalize_total_score(data.get("total_score", 0), data.get("dimension_scores")),
                "full_score": full_score,
                "dimension_scores": data.get("dimension_scores", {}),
                "overall_comment": data.get("overall_comment", ""),
                "revision_suggestions": data.get("revision_suggestions", []),
                "polish_advice": data.get("polish_advice", ""),
                "sample_essay": data.get("sample_essay", ""),
                "content": data.get("content", ""),  # 多模态识别出的作文文本，用于存入 MySQL
            }

        except (json.JSONDecodeError, ValueError) as e:
            # JSON 解析失败——多模态模式下 max_tokens 不足更常见（需额外输出 content 字段）
            logger.warning("多模态作文批改JSON解析失败，尝试用更大max_tokens重试: %s", e)
            return await self._correct_multimodal_with_retry(
                content_parts=content_parts,
                default_full_score=default_full_score,
                retry_max_tokens=MULTIMODAL_MAX_TOKENS * 2,
            )
        except Exception as e:
            logger.error("多模态作文批改失败: %s", e)
            raise

    async def _correct_multimodal_with_retry(
        self,
        content_parts: list,
        default_full_score: int,
        retry_max_tokens: int,
    ) -> dict:
        """max_tokens 翻倍重试多模态批改，解决 JSON 截断问题"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CORRECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": content_parts},
                ],
                max_tokens=retry_max_tokens,
                temperature=0.3,
                response_format={"type": "json_object"},
                timeout=240,
            )
            raw = response.choices[0].message.content or "{}"
            # 重试时用 fallback——至少返回部分可用数据
            data = _safe_parse_json(raw, fallback={})
            # 优先使用 LLM 从文件中识别到的分值，否则用默认分值
            recognized = data.get("recognized_full_score")
            full_score = int(recognized) if isinstance(recognized, (int, float)) and recognized > 0 else default_full_score
            return {
                "total_score": _normalize_total_score(data.get("total_score", 0), data.get("dimension_scores")),
                "full_score": full_score,
                "dimension_scores": data.get("dimension_scores", {}),
                "overall_comment": data.get("overall_comment", ""),
                "revision_suggestions": data.get("revision_suggestions", []),
                "polish_advice": data.get("polish_advice", ""),
                "sample_essay": data.get("sample_essay", ""),
                "content": data.get("content", ""),
            }
        except Exception as e:
            logger.error("多模态作文批改重试仍失败: %s", e)
            raise
