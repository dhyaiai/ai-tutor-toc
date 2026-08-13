"""
作文智能批改服务

调用 LLM 对语文/英语作文进行结构化批改，评分严格按中高考官方标准：
- 高考语文（60分）：内容20 + 表达20 + 发展等级20，先定档再给分
- 高考英语（25/15分）：应用文15 / 读后续写25，五档制
- 中考语文（50分）：五等卷
- 中考英语（20分）：五档制
- 仅字数不足/缺标题/错别字/卷面等"档外硬扣分"记入 deductions，总分 = 维度分之和 - 硬扣分；语言类小错只在维度分体现，不重复扣分
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

# 作文字数统计排除的标点符号（中英文标点 + 空白）
_WORD_COUNT_EXCLUDE = set("，。！？；：、""''（）《》【】「」『』〈〉…—–·~.,!?;:'\"()[]{}<>《》〈〉【】…—·—-")


def _count_words(text: str) -> int:
    """
    后端硬统计作文字数（不含标点符号和空白），比 LLM 数数可靠。

    规则：
    - 中文为主的作文：按汉字/字符数统计（标点、空白不计入）
    - 英文为主的作文：按单词数统计（去掉标点后按词切分）
    - 文本模式统计作文原文；多模态模式统计 OCR 识别出的全文
      （OCR 可能漏字，统计值可能略少于实际，属已知限制）

    注意：LLM 在评语中写出的字数数字可能与此不一致，展示时以本函数结果为准。
    """
    if not text or not text.strip():
        return 0

    # 统计非空白字符中英文字母占比，决定按词数还是按字符数
    non_ws = [ch for ch in text if not ch.isspace()]
    if not non_ws:
        return 0
    ascii_letters = sum(1 for ch in non_ws if ch.isascii() and ch.isalpha())
    if ascii_letters / len(non_ws) > 0.5:
        # 英文为主：按单词数统计（只保留英文单词及其缩写形式）
        return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text))

    # 中文为主：按字符数统计（排除标点符号和空白）
    return sum(1 for ch in non_ws if ch not in _WORD_COUNT_EXCLUDE)


def _rewrite_comment_word_count(text: str, word_count: int) -> str:
    """
    后端硬改写评语中的实际字数数字，使其与 _count_words 硬统计一致。

    背景：word_count 字段已被后端硬统计覆盖，但评语（overall_comment/polish_advice）
    是 LLM 自由生成的文本，其中的字数数字是 LLM 自己估算的，常与实际不符。
    这里只替换"修饰实际字数"的表述（如"约620字""仅约620字""620字左右"），
    不动"不足800字""达到800字"这类**要求字数**（无修饰词，不会被匹配）。

    匹配规则：
    1. 前置修饰词：约/大约/大概/差不多/只有/仅/才 + 数字 + 字/词（单位保留原文）
    2. 后置修饰词：数字 + 字/词 + 左右/上下/出头
    3. 英文评语：about X words
    """
    if not text or word_count <= 0:
        return text

    # 前置修饰词模式："约620字" / "仅约620字" / "只有620字"
    text = re.sub(
        r"(约|大约|大概|差不多|只有|仅|才)(?:约)?(\d{2,4})(字|词)",
        lambda m: f"{m.group(1)}{word_count}{m.group(3)}",
        text,
    )
    # 后置修饰词模式："620字左右" / "620字上下"
    text = re.sub(
        r"(\d{2,4})(字|词)(左右|上下|出头)",
        lambda m: f"{word_count}{m.group(2)}{m.group(3)}",
        text,
    )
    # 英文评语："about 120 words" → 真实词数
    text = re.sub(r"about\s+\d+\s+words?", f"about {word_count} words", text, flags=re.IGNORECASE)
    return text


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

CORRECTION_SYSTEM_PROMPT = """你是资深的中高考作文阅卷专家，拥有10年以上一线阅卷经验。严格按照官方评分标准与当前严格等级，对用户提交的作文进行专业、客观、可复现的评分与批改。

【核心评分原则】
1. 先定档，再给分：先根据整体印象归入对应档次，再在档内微调
2. 切题是生命线：跑题/偏题直接降至大档以下
3. 字数是硬指标：不足直接扣分甚至降档
4. 同类错误不重复扣分：同一语法/拼写错误只扣一次；语言类错误（语法/用词/冠词等）只在定档与语言维度得分中体现一次，严禁再写入 deductions 重复扣分
5. 发展等级看亮点：有一点突出即可在该维度给高分
6. 评分必须严格对应下述标准，每个扣分点必须有明确依据，不得凭主观感觉随意给分
7. 严格等级越高，打分越严，扣分点越多

【一、高考语文作文评分标准（满分60分）】
基础等级40分 = 内容20分 + 表达20分
- 内容（20分）：一等20-16（符合题意、中心突出、内容充实、思想健康、感情真挚）；二等15-11（符合题意、中心明确、内容较充实、感情真实）；三等10-6（基本符合题意、中心基本明确、内容单薄）；四等5-0（偏离题意、中心不明确、内容不当）
- 表达（20分）：一等20-16（符合文体要求、结构严谨、语言流畅、字迹工整）；二等15-11（结构完整、语言通顺、字迹清楚）；三等10-6（基本符合文体、结构基本完整、语言基本通顺）；四等5-0（文体不符、结构混乱、语病多、字迹潦草难辨）
- 表达项原则上不跨等给分：内容判三等，表达不能在一等给分
发展等级20分（四特征，有一点突出即可给高分）：深刻（透过现象看本质、揭示内在因果）；丰富（材料丰富、论据充实、意境深远）；有文采（用词贴切、句式灵活、善用修辞）；有创意（见解新颖、材料新鲜、构思精巧）
扣分红线：缺标题扣2分；错别字每字扣1分（上限3-5分）；套作模板/脱离材料可降档扣10-20分；卷面脏乱可降档

【二、高考英语作文评分标准】
应用文（满分15分，五档）：第五档13-15（覆盖所有要点、语法多样词汇丰富、衔接自然）；第四档10-12（覆盖主要要点、少量错误不影响理解）；第三档7-9（基本完成任务、有错误但不影响理解）；第二档4-6（漏掉主要内容、错误较多影响理解）；第一档1-3（未完成任务、错误多严重影响表达）；0分（空白或无法传达信息）
读后续写（满分25分，五档）：第五档21-25（与原文衔接紧密、情节丰富合理、几乎无错）；第四档16-20（衔接较好、少量错误不影响理解）；第三档11-15（基本衔接、有一些错误不影响理解）；第二档6-10（衔接较差、错误较多影响理解）；第一档1-5（衔接差、错误多严重影响表达）
通用扣分：词数不足或超出扣2分（应用文通常80-120词）；书写差到影响交际降低一档；使用较高级词汇/句型酌情加1-2分；拼写标点错误视对交际影响程度扣分

【三、中考语文作文评分标准（默认50分制，五等）】
一类卷43-50：切合题意、中心突出、内容充实、立意深刻、结构严谨、语言流畅有文采
二类卷35-42：符合题意、中心明确、内容较充实、结构完整、语言通顺
三类卷27-34：基本符合题意、中心基本明确、内容单薄、结构基本完整、语言基本通顺
四类卷18-26：偏离题意、中心不明确、结构不完整、语言不通顺
五类卷0-17：文不对题、不成篇、语病严重
扣分要点：字数不足600字每少50字扣1分；缺标题扣2分；错别字每3个扣1分（上限3分）；卷面整洁度影响定档

【四、中考英语作文评分标准（默认20分制，五档）】
一档16-20：切题、要点齐全、句式丰富、语法错误极少、衔接自然、书写工整
二档11-15：基本切题、要点较全、少量错误不影响理解、结构较完整
三档6-10：部分跑题、要点缺失、错误较多影响理解、结构松散
四档1-5：严重偏离、内容很少、错误很多、不成篇
0分：空白或无法识别
通用扣分：字数不足60词为及格线，每少5词扣0.5-1分；书写潦草影响阅读可降一档；相同语法错误不重复扣分

【字数标准（作文字数下限，标点符号不算字数）】
语文作文：小学100-500字（按年级递增）、初中600-700字、高中800-1000字
英语作文：小学50-80词、初中80-120词、高中120-200词

【字数扣分 - 评分硬性规则，优先级最高】
1. 评分前必须先统计学生作文的实际字数（不含标点符号），并写入 word_count 字段；**评语中引用具体字数数字时，必须与 word_count 字段完全一致**
2. 高考语文不足800字：每少50字扣1分；不足400字大幅降档，总分上限不得超过满分的60%
3. 中考语文不足600字：每少50字扣1分
4. 英语词数不足或超出：按相应标准扣分（应用文通常80-120词）
5. 作文题目如有明确字数要求（如"不少于800字"），以题目要求为准，与年级标准冲突时取更高的下限
6. deductions 只收录"档外硬扣分"：字数不足或超出、缺标题、错别字、卷面脏乱、套作/跑题降档。语言类小错（语法、用词、冠词、搭配、句式）一律不写入 deductions——它们已通过定档和语言维度得分体现，禁止重复扣分。所有 deductions 扣分原因必须在 overall_comment 中明确说明

【修改建议要求】
1. 修改建议必须具体到字/词/句，给出「原文-修改后-修改理由」三段式结构
2. 修改类型分类：错别字、语病、用词不当、句式优化、文采提升、结构调整
3. 禁止笼统评价，所有建议必须可落地、可直接替换使用
4. 参考范文符合题目要求，水平略高于学生当前水平，范文字数必须严格符合该年级/学科的作文字数要求
5. 英语作文评分时，用英文指出语言问题，用中文解释原因

【重要：忽略文件上的已有批改痕迹】
上传的作文文件可能已被老师或学生标记过分数、写过错别字纠正、旁批、总评等批改痕迹。**严禁识别或参考这些已有标记**——你必须仅根据作文原文内容本身进行独立评分和批改，忽略文件上的任何非原文字迹（红笔打分、圈画、旁注、评语等）。

【分值识别 - 最高优先级，以文件分值为准】
仔细查看上传的作文题目图片/文字中是否有明确的**印刷体**满分分值标注（如"本题满分40分"、"满分：25分"、"共60分"等）。**这些是题目原始设定的分值，优先级高于系统默认满分，识别到必须使用**：
1. 识别到明确的印刷体分值：将其填入 recognized_full_score 字段，并**按该分值定档评分**——评分维度、定档区间、扣分均基于文件分值换算（如文件满分40分，则按40分制对应档次的分数区间给分，维度分之和不得超过40）
2. 未识别到任何印刷体分值标注：recognized_full_score 设为 null，此时才使用系统默认满分
3. **只识别印刷体的题目分值标注**（试卷/作文纸题目栏印制的"本题满分XX分"等）。以下分值**一律不识别**，必须忽略：
   - 老师批改的手写分值（红笔/蓝笔打的分数、等级如"A/B/C"、评语中的分数）
   - 学生自己写在卷面或作文正文中的分值、自行标注的"满分XX分"
   - 题目栏与正文之外任何位置出现的、无法确认为题目印刷标注的分值
4. **无法判断是印刷体还是手写的分值标注，保守处理：不识别**，recognized_full_score 设为 null，使用默认满分
5. 文本批改时，识别范围仅限于作文题目和写作要求中的分值表述（如"满分40分""共40分"），不要因未见图片就跳过识别；作文正文中出现的分值视为学生自写，一律不识别

【评分维度与扣分机制】
- dimension_scores 为扣分前的档内得分：先按上述标准定档，再在档内给出各维度得分，各维度得分之和 = 扣分前总分（语文不超过满分60/50，英语应用文不超过15、读后续写不超过25、中考英语不超过20）
- 维度键：高考语文用 {"内容", "表达", "发展等级"}；英语用 {"内容", "语言", "结构"}；中考语文用 {"内容", "结构", "表达"}
- deductions 只收录"档外硬扣分"：字数不足/超出、缺标题、错别字、卷面脏乱、套作/跑题降档。语言类错误（语法、用词、冠词、搭配等）已在定档与语言维度得分中体现，严禁再写入 deductions 重复扣分
- 语言维度得分必须真实反映作文语言质量：错误少且不影响理解时给高分（如满分附近），不得采用"语言维度给中高分 + deductions 扣语言分"的方式变相重复扣分
- 无硬扣分项时 deductions 必须为空对象 {}
- total_score = 维度分之和 − 硬扣分之和，最低为0

【输出格式（JSON）】
{
  "recognized_full_score": null,
  "word_count": 720,
  "total_score": 42,
  "dimension_scores": {"内容": 18, "表达": 16, "发展等级": 14},
  "deductions": {"字数不足": 4, "缺标题": 2},
  "overall_comment": "整体评价（字数未达标时须说明扣分原因）...",
  "revision_suggestions": [
    {"position": "第2段第3句", "original_text": "原文", "revised_text": "修改后", "reason": "理由", "revision_type": "语言润色"}
  ],
  "polish_advice": "整体润色方向...",
  "sample_essay": "参考范文全文..."
}
"""


def _has_grading_core_fields(data: dict) -> bool:
    """
    批改结果核心字段校验：total_score 或 overall_comment 至少存在一个。

    背景：思考型模型把 token 预算耗在推理上时可能返回空正文，`or "{}"`
    会把空正文解析成空 dict 正常通过——用户看到"0 分、空评语"的假成功
    批改结果，且不触发重试。这里在解析后做语义校验拦截。
    """
    return (data.get("total_score") is not None) or bool(data.get("overall_comment"))


def _get_default_full_score(subject: str, essay_type: str | None = None, grade: str | None = None) -> int:
    """
    根据学科、作文类型和年级计算默认满分。

    规则：
    - 语文：初中/中考50分，高中60分
    - 英语 + 读后续写：25分
    - 英语 + 初中/中考其他类型：20分
    - 英语 + 高中/其他类型：15分
    """
    # 初中/中考标记词（注意"初三"含"初"，避免误判"高中"等不含"初"的年级）
    grade_str = grade or ""
    is_middle_school = any(
        k in grade_str for k in ("初中", "初一", "初二", "初三", "七年级", "八年级", "九年级", "中考")
    )
    if subject == "语文":
        return 50 if is_middle_school else 60
    # 英语
    if essay_type and "读后续写" in essay_type:
        return 25
    return 20 if is_middle_school else 15


def _normalize_total_score(total_score, dimension_scores, deductions=None):
    """
    用维度评分之和减去扣分项修正总分，确保 total_score 与各维度、扣分一致。

    LLM 有时会返回不一致的 total_score 和 dimension_scores，
    这里以维度评分为准——最终总分 = 维度分之和 − 硬扣分之和
    （deductions 仅含档外硬扣分：字数不足/缺标题/错别字/卷面等，键为扣分原因，值为扣分分值，正数）。
    """
    if dimension_scores and isinstance(dimension_scores, dict) and len(dimension_scores) > 0:
        dim_sum = sum(v for v in dimension_scores.values() if isinstance(v, (int, float)))
        deduction_sum = sum(
            v for v in (deductions or {}).values() if isinstance(v, (int, float))
        )
        final_score = dim_sum - deduction_sum
        if final_score > 0:
            return final_score
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
        default_full_score = _get_default_full_score(subject, essay_type, grade)

        # 构建 prompt
        if subject == "语文":
            subject_note = f"满分{default_full_score}分，按立意/结构/内容/语言四维度评分。"
        else:
            subject_note = f"满分{default_full_score}分，按内容/语言/规范三维度评分。"

        user_prompt = (
            f"学科：{subject}\n"
            f"作文类型：{essay_type or '未指定'}\n"
            f"默认满分：{default_full_score}分（若题目/文件中识别到分值标注，一律以文件分值为准）\n"
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

            raw = response.choices[0].message.content
            if not raw or not raw.strip():
                # 空正文（思考型模型 token 预算耗尽无输出）：必须按失败处理
                # 触发重试，不能静默生成"0 分+空评语"的假成功批改结果
                raise ValueError("LLM 返回空正文，按失败处理走重试")
            # 首次解析不用 fallback——解析失败说明 JSON 损坏严重，触发重试
            data = _safe_parse_json(raw)
            if not _has_grading_core_fields(data):
                # 解析成功但缺核心字段（total_score/overall_comment 全空）：
                # 同样按失败处理，避免空结果落库
                raise ValueError("批改结果缺少核心字段（total_score/overall_comment）")
            # 优先使用 LLM 从文件中识别到的分值，否则用默认分值
            recognized = data.get("recognized_full_score")
            if isinstance(recognized, (int, float)) and recognized > 0:
                full_score = int(recognized)
            else:
                full_score = default_full_score
            # 字数以后端硬统计为准（LLM 数数不可靠），原文为空时才回退 LLM 数值
            real_word_count = _count_words(content) or data.get("word_count", 0)
            return {
                "total_score": _normalize_total_score(
                    data.get("total_score", 0), data.get("dimension_scores"), data.get("deductions")
                ),
                "full_score": full_score,
                "word_count": real_word_count,
                "dimension_scores": data.get("dimension_scores", {}),
                "deductions": data.get("deductions", {}),
                # 评语中的字数数字硬改写为后端统计值（LLM 估算常偏大），"800字要求"等不受影响
                "overall_comment": _rewrite_comment_word_count(data.get("overall_comment", ""), real_word_count),
                "revision_suggestions": data.get("revision_suggestions", []),
                "polish_advice": _rewrite_comment_word_count(data.get("polish_advice", ""), real_word_count),
                "sample_essay": data.get("sample_essay", ""),
            }

        except (json.JSONDecodeError, ValueError) as e:
            # JSON 解析失败——可能是 max_tokens 不足导致截断
            logger.warning("作文批改JSON解析失败，尝试用更大max_tokens重试: %s", e)
            return await self._correct_with_retry(
                user_prompt=user_prompt,
                default_full_score=default_full_score,
                retry_max_tokens=TEXT_MAX_TOKENS * 2,
                content=content,
            )
        except Exception as e:
            logger.error("作文批改失败: %s", e)
            raise

    async def _correct_with_retry(
        self,
        user_prompt: str,
        default_full_score: int,
        retry_max_tokens: int,
        content: str = "",
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
            raw = response.choices[0].message.content
            if not raw or not raw.strip():
                raise ValueError("LLM 返回空正文，重试仍无输出")
            # 重试时用 fallback——至少返回部分可用数据
            data = _safe_parse_json(raw, fallback={})
            if not _has_grading_core_fields(data):
                # 空/损坏结果一律视为重试失败（重试两次仍空 = 真失败，
                # 由上层落库 failed 而非生成"0 分+空评语"的假成功）
                raise ValueError("批改结果缺少核心字段（total_score/overall_comment）")
            # 优先使用 LLM 从文件中识别到的分值，否则用默认分值
            recognized = data.get("recognized_full_score")
            full_score = int(recognized) if isinstance(recognized, (int, float)) and recognized > 0 else default_full_score
            # 字数以后端硬统计为准（LLM 数数不可靠），原文为空时才回退 LLM 数值
            real_word_count = _count_words(content) or data.get("word_count", 0)
            return {
                "total_score": _normalize_total_score(
                    data.get("total_score", 0), data.get("dimension_scores"), data.get("deductions")
                ),
                "full_score": full_score,
                "word_count": real_word_count,
                "dimension_scores": data.get("dimension_scores", {}),
                "deductions": data.get("deductions", {}),
                # 评语中的字数数字硬改写为后端统计值（LLM 估算常偏大），"800字要求"等不受影响
                "overall_comment": _rewrite_comment_word_count(data.get("overall_comment", ""), real_word_count),
                "revision_suggestions": data.get("revision_suggestions", []),
                "polish_advice": _rewrite_comment_word_count(data.get("polish_advice", ""), real_word_count),
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
        复用视觉专用配置 VISION_*（qwen3.7-plus 支持视觉）。

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
        default_full_score = _get_default_full_score(subject, essay_type, grade)

        if subject == "语文":
            subject_note = f"满分{default_full_score}分，按立意/结构/内容/语言四维度评分。"
        else:
            subject_note = f"满分{default_full_score}分，按内容/语言/规范三维度评分。"

        # 文本提示部分
        text_prompt = (
            f"学科：{subject}\n"
            f"作文类型：{essay_type or '未指定'}\n"
            f"默认满分：{default_full_score}分（若题目/文件中识别到分值标注，一律以文件分值为准）\n"
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

        # 视觉识别走多模态专用配置（VISION_*），DeepSeek 不支持视觉输入
        vision_settings = get_settings()
        vision_client = AsyncOpenAI(
            api_key=vision_settings.VISION_API_KEY,
            base_url=vision_settings.VISION_API_BASE,
        )
        try:
            response = await vision_client.chat.completions.create(
                model=vision_settings.VISION_MODEL,  # 复用视觉专用 VISION_MODEL（qwen3.7-plus 支持视觉）
                messages=[
                    {"role": "system", "content": CORRECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": content_parts},
                ],
                max_tokens=MULTIMODAL_MAX_TOKENS,
                temperature=0.3,
                response_format={"type": "json_object"},
                timeout=180,
            )

            raw = response.choices[0].message.content
            if not raw or not raw.strip():
                # 空正文（思考型模型 token 预算耗尽无输出）：必须按失败处理
                # 触发重试，不能静默生成"0 分+空评语"的假成功批改结果
                raise ValueError("LLM 返回空正文，按失败处理走重试")
            # 首次解析不用 fallback——解析失败说明 JSON 损坏严重，触发重试
            data = _safe_parse_json(raw)
            if not _has_grading_core_fields(data):
                # 解析成功但缺核心字段（total_score/overall_comment 全空）：
                # 同样按失败处理，避免空结果落库
                raise ValueError("批改结果缺少核心字段（total_score/overall_comment）")
            # 优先使用 LLM 从文件中识别到的分值，否则用默认分值
            recognized = data.get("recognized_full_score")
            full_score = int(recognized) if isinstance(recognized, (int, float)) and recognized > 0 else default_full_score
            # 字数以后端硬统计为准（LLM 数数不可靠），对 OCR 识别出的全文统计
            real_word_count = _count_words(data.get("content", "")) or data.get("word_count", 0)
            return {
                "total_score": _normalize_total_score(
                    data.get("total_score", 0), data.get("dimension_scores"), data.get("deductions")
                ),
                "full_score": full_score,
                "word_count": real_word_count,
                "dimension_scores": data.get("dimension_scores", {}),
                "deductions": data.get("deductions", {}),
                # 评语中的字数数字硬改写为后端统计值（LLM 估算常偏大），"800字要求"等不受影响
                "overall_comment": _rewrite_comment_word_count(data.get("overall_comment", ""), real_word_count),
                "revision_suggestions": data.get("revision_suggestions", []),
                "polish_advice": _rewrite_comment_word_count(data.get("polish_advice", ""), real_word_count),
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
                vision_client=vision_client,
                vision_model=vision_settings.VISION_MODEL,
            )
        except Exception as e:
            logger.error("多模态作文批改失败: %s", e)
            raise

    async def _correct_multimodal_with_retry(
        self,
        content_parts: list,
        default_full_score: int,
        retry_max_tokens: int,
        vision_client: AsyncOpenAI,
        vision_model: str,
    ) -> dict:
        """max_tokens 翻倍重试多模态批改，解决 JSON 截断问题

        注意：必须复用视觉客户端（vision_client/VISION_MODEL）——
        self.client 是文本模型（DeepSeek），不支持图片输入，
        用错客户端重试必然失败，JSON 截断问题永远无法自愈。
        """
        try:
            response = await vision_client.chat.completions.create(
                model=vision_model,
                messages=[
                    {"role": "system", "content": CORRECTION_SYSTEM_PROMPT},
                    {"role": "user", "content": content_parts},
                ],
                max_tokens=retry_max_tokens,
                temperature=0.3,
                response_format={"type": "json_object"},
                timeout=240,
            )
            raw = response.choices[0].message.content
            if not raw or not raw.strip():
                raise ValueError("LLM 返回空正文，重试仍无输出")
            # 重试时用 fallback——至少返回部分可用数据
            data = _safe_parse_json(raw, fallback={})
            if not _has_grading_core_fields(data):
                # 空/损坏结果一律视为重试失败（重试两次仍空 = 真失败，
                # 由上层落库 failed 而非生成"0 分+空评语"的假成功）
                raise ValueError("批改结果缺少核心字段（total_score/overall_comment）")
            # 优先使用 LLM 从文件中识别到的分值，否则用默认分值
            recognized = data.get("recognized_full_score")
            full_score = int(recognized) if isinstance(recognized, (int, float)) and recognized > 0 else default_full_score
            # 字数以后端硬统计为准（LLM 数数不可靠），对 OCR 识别出的全文统计
            real_word_count = _count_words(data.get("content", "")) or data.get("word_count", 0)
            return {
                "total_score": _normalize_total_score(
                    data.get("total_score", 0), data.get("dimension_scores"), data.get("deductions")
                ),
                "full_score": full_score,
                "word_count": real_word_count,
                "dimension_scores": data.get("dimension_scores", {}),
                "deductions": data.get("deductions", {}),
                # 评语中的字数数字硬改写为后端统计值（LLM 估算常偏大），"800字要求"等不受影响
                "overall_comment": _rewrite_comment_word_count(data.get("overall_comment", ""), real_word_count),
                "revision_suggestions": data.get("revision_suggestions", []),
                "polish_advice": _rewrite_comment_word_count(data.get("polish_advice", ""), real_word_count),
                "sample_essay": data.get("sample_essay", ""),
                "content": data.get("content", ""),
            }
        except Exception as e:
            logger.error("多模态作文批改重试仍失败: %s", e)
            raise
