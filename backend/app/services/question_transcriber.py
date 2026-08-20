"""
上传试题转录服务——把用户上传的试卷文件（Word/PDF/图片）转为结构化题目。

流程：
1. prepare_document：解析文件为「文本」或「图片」两种形态——
   - .docx：python-docx 提取段落 + 表格 → 文本
   - .pdf：PyMuPDF(fitz) 提取文本层；若文本量过少（扫描件）→ 渲染页面为 JPEG → 图片
   - 图片（png/jpg/jpeg/webp）→ 图片
2. transcribe：调视觉 LLM（VISION_MODEL，qwen 系列）一步完成「识别 + 拆题 + 知识点标注」，
   返回结构化 JSON（独立题 / 大题含背景材料与子题）。

设计决策（与 AI 助教既有约定一致）：
- 不走 PaddleOCR：OCR 会丢失公式/图表结构，且 paddleocr 不在 requirements 依赖中；
  视觉 LLM 多模态输入一步到位，项目已有完整先例（compositions.py 的作文图片批改、ai_grader.py 评分）。
- 不用 deepseek 文本模型做长 JSON：思考型模型推理 token 会抢占 max_tokens 预算，
  长 JSON 频繁截断（见 similar_generator.py 注释），转录统一走 VISION_MODEL。
- LLM JSON 请求统一走 llm_json.request_llm_json（重试 + 容错解析）。
"""

import asyncio
import base64
import io
import logging

logger = logging.getLogger(__name__)

# ── 输入侧限额（压缩最坏情况，防止超大文件/超长文本拖垮任务）──
MAX_TEXT_CHARS = 20000          # 文本输入截断上限（截断时附加标记提示模型）
MAX_QUESTION_COUNT = 20         # 转录题目数上限（超出部分丢弃）
PDF_MAX_PAGES_TEXT = 10         # 文本路径最多处理 10 页
PDF_MAX_PAGES_IMAGE = 4         # 扫描件渲染路径最多 4 页（多模态输入体积限制）
PDF_RENDER_SCALE = 1.5          # PDF 页面渲染缩放（与作文批改 PDF_RENDER_SCALE 一致）
PDF_TEXT_MIN_CHARS_PER_PAGE = 30  # 每页去空白后少于该字符数视为扫描件（走图片路径）

# 题型白名单：与 error_questions.py 的 _VALID_QUESTION_TYPES 保持一致，
# 筛选（收藏页/AI 挑战页）是精确匹配，转录结果必须命中白名单，否则筛不到
VALID_QUESTION_TYPES = frozenset({
    "单选题", "多选题", "选择题组", "填空题", "计算题", "应用题", "证明题",
    "简答题", "判断题", "阅读理解", "完形填空", "写作题", "作图题",
})

# 转录提示词中列出的题型白名单（帮助模型严格输出）
_QUESTION_TYPE_HINT = "、".join(sorted(VALID_QUESTION_TYPES))


def _is_image_ext(ext: str) -> bool:
    """是否为支持的图片扩展名"""
    return ext in ("png", "jpg", "jpeg", "webp")


# ═══════════════════════════════════════════════════════════════════
# 第一步：文档解析
# ═══════════════════════════════════════════════════════════════════

def _extract_docx_text(content: bytes) -> str:
    """python-docx 提取段落 + 表格文本（同步 CPU 活，调用方走 to_thread）。"""
    import docx
    document = docx.Document(io.BytesIO(content))
    parts: list[str] = []
    # 按文档顺序遍历 body 元素：段落与表格交错时保持原始顺序
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for element in document.element.body:
        tag = element.tag.split("}")[-1]
        if tag == "p":
            para = Paragraph(element, document)
            text = para.text.strip()
            if text:
                parts.append(text)
        elif tag == "tbl":
            table = Table(element, document)
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_pdf_pages(content: bytes) -> tuple[list[str], bytes | None]:
    """提取 PDF 文本层。返回 (每页文本列表, 扫描件渲染的 JPEG 字节列表或 None)。

    全部页面文本量过少 → 视为扫描版 PDF，渲染页面为 JPEG 供多模态识别。
    同步 CPU 重活（fitz 栅格化），调用方走 to_thread。
    """
    import fitz
    doc = fitz.open(stream=content, filetype="pdf")
    try:
        pages_text: list[str] = []
        for page in doc:
            text = page.get_text().strip()
            # 文本量与"第 1 页"之类的页眉混排时可能偏小，按去空白字符数判断
            compact = "".join(text.split())
            pages_text.append(text if len(compact) >= PDF_TEXT_MIN_CHARS_PER_PAGE else "")

        # 有效文本页占比：少于一半视为扫描件
        valid_pages = sum(1 for t in pages_text if t)
        if valid_pages < max(1, len(pages_text) // 2):
            images = []
            for i, page in enumerate(doc):
                if i >= PDF_MAX_PAGES_IMAGE:
                    break
                mat = fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE)
                pix = page.get_pixmap(matrix=mat)
                images.append(pix.tobytes("jpeg"))
            return [], images
        return pages_text, None
    finally:
        doc.close()


def _truncate_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    """截断超长文本并附加标记，提示模型内容不完整。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n（内容过长已截断，仅保留前 {max_chars} 字符）"


async def prepare_document(content: bytes, filename: str) -> dict:
    """
    把上传文件解析为统一输入形态。

    返回 {"kind": "text", "text": str} 或 {"kind": "images", "images": [JPEG bytes, ...]}。
    每步解析都在线程池执行（同步 CPU 活）+ 60s 超时，防止卡死任务。
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "docx":
        text = await asyncio.wait_for(
            asyncio.to_thread(_extract_docx_text, content), timeout=60
        )
        if not text.strip():
            raise ValueError("Word 文件未提取到任何文字，请检查文件内容")
        return {"kind": "text", "text": _truncate_text(text)}

    if ext == "pdf":
        pages_text, page_images = await asyncio.wait_for(
            asyncio.to_thread(_extract_pdf_pages, content), timeout=60
        )
        if page_images:
            # 扫描版 PDF：渲染为页面图片走多模态
            return {"kind": "images", "images": page_images}
        text = "\n".join(t for t in pages_text if t)
        if not text.strip():
            raise ValueError("PDF 未提取到可识别的文字")
        return {"kind": "text", "text": _truncate_text(text)}

    if _is_image_ext(ext):
        return {"kind": "images", "images": [content]}

    raise ValueError(f"不支持的文件类型：{ext}")


# ═══════════════════════════════════════════════════════════════════
# 第二步：LLM 转录
# ═══════════════════════════════════════════════════════════════════

# 转录输出 JSON schema 说明（文本与图片路径共用）
_SCHEMA_DESC = f"""输出必须是一个 JSON 对象，结构如下（严禁输出 JSON 以外的任何文字）：
{{
  "questions": [
    {{
      "is_big_question": false,
      "question_text": "题干（含 $...$ LaTeX 公式）",
      "answer": "正确答案（多选用逗号分隔字母，如 A,C,D）",
      "analysis": "完整解析（解题思路、关键步骤、依据）",
      "question_type": "题型（必须取自以下白名单之一：{_QUESTION_TYPE_HINT}）",
      "knowledge_point": "知识点（不超过 50 字）",
      "options": [{{"label": "A", "text": "选项内容"}}, ...],
      "children": null
    }}
  ]
}}

大题（有背景材料/阅读文本/共用题干）格式：
{{
  "is_big_question": true,
  "question_context": "背景材料或阅读文本",
  "children": [
    {{"question_text": "第(1)问题干", "answer": "...", "analysis": "...",
      "question_type": "...", "knowledge_point": "...", "options": [...]}}
  ]
}}
"""

TRANSCRIBE_SYSTEM_PROMPT = f"""你是一位经验丰富的中学试题转录助手。你的任务是把用户上传的试卷内容转录为结构化题目数据，供在线题库使用。

规则：
1. 忠实转录原题内容，不得修改题意、不得增删信息。公式一律用 $...$ 包裹的 LaTeX 表达（与前端 KaTeX 渲染一致）。
2. 题型 question_type 必须取自以下白名单：{_QUESTION_TYPE_HINT}。用户选择的题型是参考值，以题目实际题型为准；无法判断时使用用户选择的值。
3. 选择题/多选题必须提供 options（label 用 A/B/C/D 连续编号，text 为选项内容）；多选题 answer 用逗号分隔所有正确选项字母。
4. 判断题答案用"正确/错误"；填空题如有多个空，用"（1）xxx（2）xxx"说明；写作题 answer 给出写作要点与结构建议。
5. 大题判定：题干前有材料/阅读文本/共用题干时 is_big_question 为 true，材料放 question_context，小题逐一放 children。
6. 【重要】试卷中的图形内容（几何图、函数图、电路图、统计图表、图片题等）若无法用文字完整描述，必须在对应题干末尾注明"（原题含图，请参考原题）"，严禁编造图形信息或在题干中虚构图中数据。
7. knowledge_point 标注主要考察知识点（不超过 50 字）；analysis 必须完整（解题思路、关键步骤、答案依据），不能只写答案。
8. 内容不足以成题时 questions 可为空数组，不得虚构题目。
9. 每道题必须保留题号顺序，原卷中的题号（如"1."、"第2题"）不需要转录进题干。

{_SCHEMA_DESC}"""


def _build_user_prompt(doc: dict, meta: dict) -> str:
    """构建用户提示：元数据 + 输入内容（文本或图片描述）。"""
    meta_hint = (
        f"用户填写的信息（用于辅助判断题型与知识点）："
        f"{meta.get('grade', '')} {meta.get('subject', '')} {meta.get('semester', '')}，"
        f"用户选择的题型：{meta.get('question_type', '未知')}"
    )
    if doc["kind"] == "text":
        return (
            f"{meta_hint}\n\n"
            f"以下是试卷文字内容，请转录为 JSON：\n"
            f"----------\n{doc['text']}\n----------"
        )
    return f"{meta_hint}\n\n以下是试卷页面图片（共 {len(doc['images'])} 页），请识别并转录为 JSON。"


async def transcribe(doc: dict, meta: dict) -> list[dict]:
    """
    调用视觉 LLM 转录并归一化题目列表。

    :param doc: prepare_document 的返回值
    :param meta: {"grade", "subject", "semester", "question_type"}
    :return: 归一化后的题目 dict 列表（结构与 AIGeneratedQuestion 字段对齐）
    :raises: RuntimeError（缺 key）/ ValueError（转录结果无效）/ 其他 LLM 异常
    """
    from openai import AsyncOpenAI
    from app.core.config import get_settings
    from app.services.llm_json import request_llm_json

    settings = get_settings()
    if not settings.VISION_API_KEY:
        # 缺 key 时立即明确报错（与 ai_grader 一致），而不是让请求失败后暴露晦涩错误
        raise RuntimeError("多模态模型未配置：请在 .env 中设置 VISION_API_KEY")

    client = AsyncOpenAI(
        api_key=settings.VISION_API_KEY,
        base_url=settings.VISION_API_BASE,
    )

    # 组装 messages：文本路径单条文本消息；图片路径 content 数组（text + 多页图片）
    user_text = _build_user_prompt(doc, meta)
    if doc["kind"] == "images":
        content: list[dict] = [{"type": "text", "text": user_text}]
        for img in doc["images"]:
            b64 = base64.b64encode(img).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        messages = [
            {"role": "system", "content": TRANSCRIBE_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
    else:
        messages = [
            {"role": "system", "content": TRANSCRIBE_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ]

    result = await request_llm_json(
        client,
        model=settings.VISION_MODEL,
        messages=messages,
        max_tokens=8000,          # 多题 + 大题嵌套 JSON 的预算（与评分器 GRADER_MAX_OUTPUT_TOKENS 一致）
        temperature=0.3,
        timeout=180,              # 单次调用超时；request_llm_json 内部最多重试 3 次
        attempts=3,
        retry_delay=1.0,
        response_format={"type": "json_object"},
        extract_braces=True,      # 容错：模型附带多余文本时提取 JSON
        # qwen3.7 系列默认开启思考模式，推理 token 抢占输出预算导致多题 JSON 截断/空壳；
        # 转录是结构化 JSON 输出，关闭思考更稳定（与评分器 ai_grader 一致）。
        extra_body={"enable_thinking": settings.VISION_ENABLE_THINKING},
    )
    if result.data is None:
        raise ValueError(f"转录失败：{result.error or '模型未返回有效内容'}")

    raw_questions = result.data.get("questions")
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("未能识别出有效题目（转录结果为空）")

    return _normalize_questions(raw_questions, meta)


def _normalize_questions(raw_questions: list, meta: dict) -> list[dict]:
    """
    归一化转录结果：
    - 过滤题干/答案为空的题
    - 题型白名单校验，不命中回落用户表单选择，仍无效置 None
    - knowledge_point 截断 255 字符（列宽限制）
    - options label 连续重排 + 过滤空选项
    - 大题子题递归同规则
    - 上限 MAX_QUESTION_COUNT 道
    """
    def normalize_options(raw) -> list[dict] | None:
        if not isinstance(raw, list):
            return None
        cleaned: list[dict] = []
        for i, opt in enumerate(raw):
            if not isinstance(opt, dict):
                continue
            text = str(opt.get("text", "")).strip()
            if text:
                cleaned.append({"label": chr(65 + i), "text": text})
        return cleaned or None

    def normalize_type(raw) -> str | None:
        value = str(raw or "").strip()
        if value in VALID_QUESTION_TYPES:
            return value
        # 回落用户表单选择的题型
        fallback = str(meta.get("question_type") or "").strip()
        return fallback if fallback in VALID_QUESTION_TYPES else None

    def normalize_one(raw: dict, fallback_type: str | None) -> dict | None:
        if not isinstance(raw, dict):
            return None
        question_text = str(raw.get("question_text") or "").strip()
        answer = str(raw.get("answer") or "").strip()
        question_type = normalize_type(raw.get("question_type")) or fallback_type
        # 大题：children 子题递归归一化（大题本身无题干/答案字段，
        # 不能按独立题的空字段校验直接丢弃）
        children_raw = raw.get("children")
        if raw.get("is_big_question") and isinstance(children_raw, list) and children_raw:
            children = []
            for c in children_raw:
                child = normalize_one(c, question_type)
                if child:
                    children.append(child)
            if children:
                return {
                    "is_big_question": True,
                    "question_context": str(raw.get("question_context") or "").strip() or None,
                    "question_type": question_type,
                    "knowledge_point": (str(raw.get("knowledge_point") or "").strip())[:255] or None,
                    "children": children,
                }
        # 独立题：题干/答案缺失视为转录残缺，直接丢弃
        if not question_text or not answer:
            return None
        return {
            "question_text": question_text,
            "answer": answer,
            "analysis": str(raw.get("analysis") or "").strip() or None,
            "question_type": question_type,
            "knowledge_point": (str(raw.get("knowledge_point") or "").strip())[:255] or None,
            "options": normalize_options(raw.get("options")),
            "is_big_question": False,
            "children": None,
        }

    normalized: list[dict] = []
    for raw in raw_questions:
        item = normalize_one(raw, None)
        if item:
            normalized.append(item)
        if len(normalized) >= MAX_QUESTION_COUNT:
            logger.warning("转录题目数超过上限 %d，后续题目已丢弃", MAX_QUESTION_COUNT)
            break
    if not normalized:
        raise ValueError("未能识别出有效题目（转录内容不完整）")
    return normalized
