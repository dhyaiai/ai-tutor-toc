"""
口语测评服务

覆盖 3 个子功能：
- 英语听力训练：生成题目 + 批改
- 单词听写：生成任务 + 批改
- 普通话测评：评测评分（朗读模式使用讯飞流式语音评测，其余由 LLM 驱动）

题目生成和批改由 LLM 驱动。
TTS 语音播报使用浏览器内置 SpeechSynthesis（无需后端）。
"""

import asyncio
import base64
import json
import logging
import re
from openai import AsyncOpenAI
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _mandarin_level(score: float) -> str:
    """按普通话水平测试标准将百分制得分映射为等级"""
    if score >= 97:
        return "一级甲等"
    if score >= 92:
        return "一级乙等"
    if score >= 87:
        return "二级甲等"
    if score >= 80:
        return "二级乙等"
    if score >= 70:
        return "三级甲等"
    if score >= 60:
        return "三级乙等"
    return "未入级"


class OralService:
    """口语测评统一服务"""

    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_API_BASE,
        )
        self.model = settings.LLM_MODEL

    # ============ 英语听力 ============

    async def generate_listening_test(
        self, question_type: str, difficulty: str = "中等",
        question_count: int = 5, grade: str | None = None,
        grade_level: str | None = None,
    ) -> dict:
        """生成英语听力试卷。

        短对话题型：每道题自带一段独立 dialogue（M:/W: 标签），
        播放时逐题独立播放、M 男声 W 女声。
        长对话/短文理解：仍共用一段顶层 transcript。
        """
        grade_hint = f"学段：{grade_level}，难度：{difficulty}" if grade_level else f"难度：{difficulty}"

        if question_type == "短对话":
            # 短对话：每题生成独立对话，M/W 标签分行，播放时分别用男女声
            prompt = (
                f"请生成 {question_count} 道英语听力短对话题，{grade_hint}。\n"
                f"要求：\n"
                f"1. 每道题包含一段独立的英文短对话（dialogue），用 M: 和 W: 分行标注男女说话人（2~5 轮对话），"
                f"对话内容应自然、信息量足以支撑出题。\n"
                f"2. 每道题包含 1 个三选一理解题（question + options 对象 + answer）。\n"
                f"3. 题目必须能【仅凭该题的 dialogue】作答。\n"
                f"请严格以 JSON 格式返回，options 必须是对象 {{'A': '...', 'B': '...', 'C': '...'}}，不是数组：\n"
                f"{{\"question_type\": \"短对话\", \"questions\": [{{"
                f"\"dialogue\": \"M: ...\\nW: ...\\nM: ...\", "
                f"\"question\": \"问题\", "
                f"\"options\": {{\"A\": \"...\", \"B\": \"...\", \"C\": \"...\"}}, "
                f"\"answer\": \"A\"}}]}}"
            )
        elif question_type == "短文理解":
            passage_hint = "transcript 为一段连贯的英文短文（约 80~150 词），不需要说话人标签。"
            prompt = (
                f"请先创作一段英语听力{question_type}原文，再从原文中提取 {question_count} 道理解题。\n"
                f"年级：{grade or '未指定'}\n"
                f"{grade_hint}\n"
                f"原文要求：{passage_hint}\n"
                f"题目要求：每道题必须能【仅凭该段原文】作答，包含 问题、3个选项（A/B/C）、正确答案。\n"
                f"请严格以JSON格式返回，transcript 为整段听力原文（字符串），options 必须是对象而非数组：\n"
                f"{{\"transcript\": \"整段英文听力原文\", "
                f"\"questions\": [{{"
                f"\"question\": \"问题\", "
                f"\"options\": {{\"A\": \"...\", \"B\": \"...\", \"C\": \"...\"}}, "
                f"\"answer\": \"A\"}}]}}"
            )
        else:
            # 长对话：共用一段 transcript
            passage_hint = (
                "transcript 为一段英文对话，请用说话人标签分行，例如\n"
                "“M: ...\\nW: ...\\nM: ...”。"
                "长对话保持 6~10 轮，"
                "对话要自然、信息量足以支撑出题。"
            )
            prompt = (
                f"请先创作一段英语听力{question_type}原文，再从原文中提取 {question_count} 道理解题。\n"
                f"年级：{grade or '未指定'}\n"
                f"{grade_hint}\n"
                f"原文要求：{passage_hint}\n"
                f"题目要求：每道题必须能【仅凭该段原文】作答，包含 问题、3个选项（A/B/C）、正确答案。\n"
                f"请严格以JSON格式返回，transcript 为整段听力原文（字符串），options 必须是对象而非数组：\n"
                f"{{\"transcript\": \"整段英文听力原文\", "
                f"\"questions\": [{{"
                f"\"question\": \"问题\", "
                f"\"options\": {{\"A\": \"...\", \"B\": \"...\", \"C\": \"...\"}}, "
                f"\"answer\": \"A\"}}]}}"
            )
        data = await self._call_llm(prompt, max_tokens=4000)
        return self._normalize_listening(data)

    @staticmethod
    def _normalize_listening(data: dict) -> dict:
        """规范化听力题结构，确保 options 为对象、answer 为选项字母。

        短对话题型：每题自带 dialogue，无需顶层 transcript。
        长对话/短文理解：共用顶层 transcript，题目不各自携带 scene。
        LLM 有时会把 options 返回为数组（[{label, text}]），前端按对象渲染会崩溃导致白屏。
        """
        if not isinstance(data, dict):
            return {"transcript": "", "questions": []}
        questions = data.get("questions") or []
        question_type = data.get("question_type", "")

        # 判断是否为短对话模式（每题自带 dialogue）
        has_per_question_dialogue = any(
            isinstance(q, dict) and q.get("dialogue") for q in questions
        )

        if has_per_question_dialogue:
            # 短对话模式：每题独立 dialogue，无需顶层 transcript
            transcript = ""
        else:
            # 长对话/短文理解模式：共用顶层 transcript
            transcript = data.get("transcript") or data.get("scene") or ""
            for q in questions:
                if not isinstance(q, dict):
                    continue
                if not transcript and q.get("scene"):
                    transcript = q.get("scene") or ""
                # 单篇原文模式下题目不再各自携带 scene，避免前端误播单题片段
                q.pop("scene", None)

        for q in questions:
            if not isinstance(q, dict):
                continue
            opts = q.get("options")
            if isinstance(opts, list):
                new_opts: dict = {}
                for idx, item in enumerate(opts):
                    label = chr(65 + idx)
                    if isinstance(item, dict):
                        label = item.get("label") or label
                        new_opts[label] = item.get("text", "")
                    else:
                        new_opts[label] = str(item)
                q["options"] = new_opts
            if not q.get("answer") and q.get("correct_answer"):
                q["answer"] = q.get("correct_answer")
        data["transcript"] = transcript
        data["questions"] = questions
        return data

    async def submit_listening_answers(self, questions: list, answers: list) -> dict:
        """批改听力答案。

        英语听力每题默认 1.5 分，返回总分值而非正确个数。
        除总体成绩外，额外返回逐题明细 details（题干/选项/作答/正确答案/是否正确/得分），
        供前端展示逐题对错并保存进作业记录。
        """
        total = len(questions)
        correct = 0
        PER_QUESTION_SCORE = 1.5  # 英语听力每题默认 1.5 分
        full_score = total * PER_QUESTION_SCORE
        details = []
        wrong_details = []
        for i, q in enumerate(questions):
            user_ans = answers[i] if i < len(answers) else ""
            correct_ans = str(q.get("answer", "") or q.get("correct_answer", ""))
            is_correct = user_ans.strip().upper() == correct_ans.strip().upper()
            if is_correct:
                correct += 1
            detail = {
                "question_id": i + 1,
                "question": q.get("question", ""),
                "options": q.get("options", {}),
                "user_answer": user_ans,
                "correct_answer": correct_ans,
                "is_correct": is_correct,
                "score": PER_QUESTION_SCORE if is_correct else 0,  # 每题得分
            }
            # 短对话模式：保留每题 dialogue 供记录详情展示
            if q.get("dialogue"):
                detail["dialogue"] = q.get("dialogue")
            details.append(detail)
            if not is_correct:
                wrong_details.append(detail)
        total_score = correct * PER_QUESTION_SCORE
        rate = correct / total if total > 0 else 0
        return {
            "total": total,
            "total_score": total_score,   # 实际得分
            "full_score": full_score,     # 满分
            "correct_rate": rate,
            "grade": self._rate_to_grade(rate),
            "details": details,
            "wrong_details": wrong_details,
        }

    @staticmethod
    def _rate_to_grade(rate: float) -> str:
        """正确率转评级"""
        if rate >= 0.9:
            return "优秀"
        if rate >= 0.7:
            return "良好"
        if rate >= 0.6:
            return "及格"
        return "待提高"

    # ============ 单词听写 ============

    async def generate_dictation_task(
        self, word_scope: str, word_count: int = 10, direction: str = "汉译英",
        difficulty: str = "中等",
    ) -> dict:
        """生成单词听写任务。

        与英语听力一致：AI 生成一段“老师口语化播报”的听写任务文本（broadcast_text，中英夹杂），
        并同时给出答案词表（words，每词携带自己的测试方向 prompt_lang）供批改使用。

        direction:
        - 汉译英：老师报中文释义，学生写英文单词（播报文本中不得出现该词英文拼写）
        - 英译汉：老师读英文单词，学生写中文释义
        - 默写单词：老师朗读英文单词，学生默写出同一个英文单词
        - 中英混合：同一份任务里，部分单词汉译英、部分英译汉，混合出现

        difficulty（简单/中等/困难）：控制所选单词的难易程度。
        """
        if direction == "英译汉":
            dir_rule = (
                "所有单词均为『英译汉』：老师朗读英文单词（连续朗读三遍即可），学生写中文释义。"
                "每个单词的 prompt_lang 固定为 \"英译汉\"。播报文本中要出现英文单词本身，但不要出现中文释义。"
            )
        elif direction == "默写单词":
            dir_rule = (
                "所有单词均为『默写单词』：老师清晰朗读英文单词（连续朗读三遍即可），学生默写出同一个英文单词。"
                "每个单词的 prompt_lang 固定为 \"默写单词\"。播报文本中必须出现该英文单词本身（这是要默写的目标词），"
                "但不要出现中文释义。"
            )
        elif direction == "中英混合":
            dir_rule = (
                "本任务为『中英混合』：请把单词随机分成两类混合排列——"
                "一类为『汉译英』（prompt_lang=\"汉译英\"，老师报中文释义、学生写英文，播报中不得出现该词英文拼写）；"
                "另一类为『英译汉』（prompt_lang=\"英译汉\"，老师读英文单词、学生写中文释义，播报中不得出现该词中文释义）。"
                "两类都要有，交替或随机出现，并在播报时明确告诉学生这一题是写英文还是写中文。"
            )
        else:  # 汉译英
            direction = "汉译英"
            dir_rule = (
                "所有单词均为『汉译英』：老师只报中文释义（可加一句中文提示），学生写英文单词。"
                "每个单词的 prompt_lang 固定为 \"汉译英\"。"
                "【严禁泄露答案】播报文本中绝对不能出现该单词的英文拼写：既不能把英文单词朗读出来"
                "（禁止出现类似“grateful，grateful，grateful”这样的重复朗读），例句、搭配、提示里也一律不得包含该英文单词。"
            )
        diff_rule = {
            "简单": "难度=简单：请只选取该范围内最常见、拼写较短、易于掌握的基础词汇。",
            "困难": "难度=困难：请选取该范围内较生僻、拼写较长或容易拼错的高阶词汇。",
        }.get(difficulty, "难度=中等：请选取该范围内难度适中的常用词汇。")
        prompt = (
            f"你是一位英语老师，正在给学生进行单词听写。请设计一份包含 {word_count} 个单词的听写任务。\n"
            f"单词范围：{word_scope}\n"
            f"难度要求：{diff_rule}\n"
            f"测试方向：{direction}。{dir_rule}\n"
            f"请生成两部分内容：\n"
            f"1. broadcast_text：一段口语化、自然的老师播报文本（中英夹杂），像老师在课堂上口头报听写一样，"
            f"逐个报出编号和每个单词的提示（第1个、第2个……）。语气自然亲切，可有适当过渡语。"
            f"务必与下面的 words 列表逐条对应、顺序一致，数量相同。\n"
            f"【重要】凡需要朗读英文单词的题目，只需把该单词连续清晰地朗读三遍即可，"
            f"严禁逐字母拼读（绝不能出现类似 a-p-p-l-e、b-e-a-u-t-i-f-u-l 这样的逐字母拼写形式）。\n"
            f"2. words：答案词表，数组，每项包含 english（英文拼写）、chinese（中文释义）、pos（词性）、"
            f"prompt_lang（该词测试方向：\"汉译英\"、\"英译汉\" 或 \"默写单词\"）。\n"
            f"请严格以 JSON 返回：\n"
            f"{{\"broadcast_text\": \"同学们好，现在开始听写……\", "
            f"\"words\": [{{\"english\": \"apple\", \"chinese\": \"苹果\", \"pos\": \"名词\", \"prompt_lang\": \"汉译英\"}}]}}"
        )
        data = await self._call_llm(prompt, max_tokens=3000)
        return self._normalize_dictation(data, direction)

    @staticmethod
    def _normalize_dictation(data: dict, direction: str) -> dict:
        """规范化听写任务结构，确保 words 每词携带合法 prompt_lang。

        非中英混合模式下，所有词的 prompt_lang 强制对齐顶层 direction；
        中英混合保留 AI 返回的逐词 prompt_lang，非法值回退为汉译英。
        """
        if not isinstance(data, dict):
            return {"broadcast_text": "", "words": [], "direction": direction}
        words = data.get("words") or []
        if direction == "英译汉":
            default_lang = "英译汉"
        elif direction == "默写单词":
            default_lang = "默写单词"
        else:
            default_lang = "汉译英"
        norm_words = []
        for w in words:
            if not isinstance(w, dict):
                continue
            lang = w.get("prompt_lang")
            if direction != "中英混合" or lang not in ("汉译英", "英译汉"):
                lang = default_lang
            norm_words.append({
                "english": (w.get("english") or w.get("word") or "").strip(),
                "chinese": (w.get("chinese") or w.get("meaning") or "").strip(),
                "pos": w.get("pos") or "",
                "prompt_lang": lang,
            })
        return {
            "broadcast_text": OralService._sanitize_broadcast(
                data.get("broadcast_text") or data.get("transcript") or "", norm_words
            ),
            "words": norm_words,
            "direction": direction,
        }

    @staticmethod
    def _sanitize_broadcast(broadcast_text: str, words: list) -> str:
        """兜底移除播报文本里泄露的答案，避免 TTS 把答案读出来。

        LLM 有时会无视“不得泄露答案”的提示，因此在此做确定性清洗：
        - 汉译英：答案是英文拼写，逐词按整词（不区分大小写）掩盖英文；
        - 英译汉：答案是中文释义，逐个义项掩盖（跳过单字，避免误删常用字）。
        掩盖后清理占位符导致的连续/悬挂标点，保持播报自然。
        """
        if not broadcast_text or not words:
            return broadcast_text or ""
        text = broadcast_text
        for w in words:
            if w.get("prompt_lang") == "默写单词":
                # 默写单词：听英文写英文，目标词本身就是要听到的内容，不得掩盖
                continue
            if w.get("prompt_lang") == "英译汉":
                # 答案为中文释义，逐个义项掩盖
                for term in re.split(r"[，,、；;/\s]+", w.get("chinese", "") or ""):
                    term = term.strip()
                    if len(term) >= 2:
                        text = text.replace(term, "▢")
            else:  # 汉译英：答案为英文拼写
                term = (w.get("english", "") or "").strip()
                if term:
                    text = re.sub(
                        r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])",
                        "▢", text, flags=re.IGNORECASE,
                    )
        # 折叠“▢，▢，▢”这类重复朗读，再移除占位符并清理多余标点
        text = re.sub(r"▢(?:[，,、\s]*▢)+", "▢", text)
        text = text.replace("▢", "")
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"，[，、\s]+", "，", text)
        text = re.sub(r"[，、\s]+([。！？])", r"\1", text)
        text = re.sub(r"。+", "。", text)
        text = re.sub(r"([！？])\1+", r"\1", text)
        text = re.sub(r"^[，、。\s]+", "", text)
        return text.strip()

    async def submit_dictation_result(
        self, words: list, user_spellings: list, direction: str = "汉译英",
    ) -> dict:
        """批改听写结果（键盘输入，按行与单词逐条对应匹配）。

        每个单词按其 prompt_lang 判定（顶层 direction 仅作缺省回退）：
        - 汉译英: 学生写英文，比对 english（忽略大小写/首尾空格）
        - 英译汉: 学生写中文释义，比对 chinese（忽略首尾空格；支持多释义命中其一）
        - 默写单词: 学生默写英文，比对 english（忽略大小写/首尾空格）
        """
        total = len(words)
        correct = 0
        wrong_words = []
        details = []
        fallback_lang = direction if direction in ("汉译英", "英译汉", "默写单词") else "汉译英"
        for i, w in enumerate(words):
            user_spell = (user_spellings[i] if i < len(user_spellings) else "") or ""
            lang = w.get("prompt_lang") or fallback_lang
            if lang == "英译汉":
                correct_answer = w.get("chinese", "")
                is_correct = self._match_chinese(user_spell, correct_answer)
                question = w.get("english", "")
            else:
                # 汉译英 / 默写单词：答案均为英文拼写
                correct_answer = w.get("english", "")
                is_correct = user_spell.strip().lower() == correct_answer.strip().lower()
                question = w.get("english", "") if lang == "默写单词" else w.get("chinese", "")
            if is_correct:
                correct += 1
            else:
                wrong_words.append({
                    "word": w.get("english", ""),
                    "chinese": w.get("chinese", ""),
                    "user_spelling": user_spell.strip(),
                    "correct_spelling": correct_answer,
                    "prompt_lang": lang,
                })
            details.append({
                "index": i + 1,
                "prompt_lang": lang,
                "english": w.get("english", ""),
                "chinese": w.get("chinese", ""),
                "question": question,
                "correct_answer": correct_answer,
                "user_answer": user_spell.strip(),
                "is_correct": is_correct,
            })
        return {
            "total": total,
            "correct_count": correct,
            "wrong_count": total - correct,
            "wrong_words": wrong_words,
            "details": details,
        }

    @staticmethod
    def _match_chinese(user: str, answer: str) -> bool:
        """中文释义匹配：完全相等，或答案含多个释义（多种分隔符）时命中其一。"""
        import re
        u = (user or "").strip()
        a = (answer or "").strip()
        if not u:
            return False
        if u == a:
            return True
        parts = [p.strip() for p in re.split(r"[、，,；;/\s]+", a) if p.strip()]
        return u in parts

    async def grade_dictation_image(
        self, words: list, image_base64: str, image_mime: str = "image/jpeg",
    ) -> dict:
        """使用多模态LLM识别学生上传的听写作答图片并批改。

        将答案词表和作答图片一起发给多模态模型，由模型识别每个编号对应的学生作答，
        并与答案逐词比对，返回与键盘批改一致的结构。
        """
        key_lines = []
        for i, w in enumerate(words):
            lang = w.get("prompt_lang") or "汉译英"
            if lang == "英译汉":
                ask = f"（英译汉：学生应写中文释义，正确答案：{w.get('chinese', '')}）"
            elif lang == "默写单词":
                ask = f"（默写单词：学生应默写英文单词，正确答案：{w.get('english', '')}）"
            else:
                ask = f"（汉译英：学生应写英文单词，正确答案：{w.get('english', '')}）"
            key_lines.append(
                f"{i + 1}. english={w.get('english', '')} chinese={w.get('chinese', '')} {ask}"
            )
        key_text = "\n".join(key_lines)

        instruction = (
            "这是一张学生手写的英语单词听写作答图片。请完成：\n"
            "1. 按题号识别学生每一题写的内容（user_answer）。\n"
            "2. 对照下面的答案词表逐题判断对错（英文忽略大小写，中文释义命中其一即算对）。\n"
            f"答案词表（共 {len(words)} 题）：\n{key_text}\n"
            "请严格以JSON返回：\n"
            "{\"items\": [{\"index\": 1, \"user_answer\": \"识别到的作答\", \"is_correct\": true}]}"
        )
        content_parts = [
            {"type": "text", "text": instruction},
            {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_base64}"}},
        ]
        # 视觉识别走多模态专用配置（VISION_*），DeepSeek 不支持视觉输入
        vision_settings = get_settings()
        vision_client = AsyncOpenAI(
            api_key=vision_settings.VISION_API_KEY,
            base_url=vision_settings.VISION_API_BASE,
        )
        try:
            response = await vision_client.chat.completions.create(
                model=vision_settings.VISION_MODEL,
                messages=[{"role": "user", "content": content_parts}],
                max_tokens=2000,
                temperature=0.1,
                response_format={"type": "json_object"},
                timeout=180,
            )
            data = json.loads(response.choices[0].message.content or "{}")
        except Exception as e:
            logger.error("听写图片批改LLM调用失败: %s", e)
            return {
                "total": len(words), "correct_count": 0, "wrong_count": len(words),
                "wrong_words": [], "error": str(e),
            }

        by_index = {}
        for it in (data.get("items") or []):
            try:
                by_index[int(it.get("index"))] = it
            except (TypeError, ValueError):
                continue

        total = len(words)
        correct = 0
        wrong_words = []
        details = []
        for i, w in enumerate(words):
            it = by_index.get(i + 1, {})
            user_ans = str(it.get("user_answer", "") or "").strip()
            is_correct = bool(it.get("is_correct"))
            lang = w.get("prompt_lang") or "汉译英"
            correct_answer = w.get("chinese", "") if lang == "英译汉" else w.get("english", "")
            if lang == "英译汉":
                question = w.get("english", "")
            elif lang == "默写单词":
                question = w.get("english", "")
            else:
                question = w.get("chinese", "")
            if is_correct:
                correct += 1
            else:
                wrong_words.append({
                    "word": w.get("english", ""),
                    "chinese": w.get("chinese", ""),
                    "user_spelling": user_ans,
                    "correct_spelling": correct_answer,
                    "prompt_lang": lang,
                })
            details.append({
                "index": i + 1,
                "prompt_lang": lang,
                "english": w.get("english", ""),
                "chinese": w.get("chinese", ""),
                "question": question,
                "correct_answer": correct_answer,
                "user_answer": user_ans,
                "is_correct": is_correct,
            })
        return {
            "total": total,
            "correct_count": correct,
            "wrong_count": total - correct,
            "wrong_words": wrong_words,
            "details": details,
        }

    # ============ 普通话测评 ============

    async def generate_mandarin_text(
        self, topic: str = "", difficulty: str = "中等", length: str = "短",
    ) -> dict:
        """生成普通话朗读文本（AI生成文本模式）。

        参数：
        - topic: 话题（如"日常对话"、"新闻播报"、"散文朗诵"），留空则随机
        - difficulty: 难度（简单/中等/困难）
        - length: 长度（短/中/长），对应约 80~150 / 150~300 / 300~500 字
        """
        length_hint = {"短": "80~150字", "中": "150~300字", "长": "300~500字"}.get(length, "100~200字")
        topic_hint = f"话题：{topic}" if topic else "话题不限，随机选择一个日常场景"
        prompt = (
            f"请生成一段适合普通话朗读测评的中文文本。\n"
            f"{topic_hint}\n"
            f"难度：{difficulty}（简单=常见词汇短句，中等=包含一些多音字和轻声词，困难=包含易错读音、儿化音、绕口令元素）\n"
            f"长度：{length_hint}\n"
            f"要求：文本应自然流畅，包含完整的语境（如一段对话、一段新闻、一段散文等），"
            f"适合用来评测普通话发音标准度。\n"
            f"请严格以JSON格式返回："
            f"{{\"text\": \"生成的朗读文本内容...\", \"topic\": \"文本话题\", "
            f"\"difficulty\": \"{difficulty}\", \"pinyin_notes\": [{{\"char\": \"长\", \"pinyin\": \"cháng\", \"note\": \"多音字提示\"}}]}}"
        )
        data = await self._call_llm(prompt, max_tokens=2000)
        # 确保返回结构一致
        if not isinstance(data, dict) or "text" not in data:
            return {"text": "", "topic": "", "difficulty": difficulty, "error": "生成文本失败"}
        return data

    async def evaluate_mandarin(
        self, test_level: str, text_content: str,
        audio_base64: str, audio_format: str = "wav",
        personality_directive: str | None = None,
    ) -> dict:
        """普通话朗读测评（讯飞流式语音评测）。

        用户朗读 AI 生成的参考文本，录音由讯飞 ISE 引擎评测发音；
        评分维度（百分制）：声韵（发音）、调型（声调）、流畅度、完整度；
        评语与改进建议由 LLM 基于讯飞评测数据生成（失败则规则兜底）。

        前置要求：
        - audio_base64: 16k/16bit/单声道 WAV（前端录音后已转码）
        - text_content: 朗读参考文本（必填）
        参数缺失、格式不符或讯飞调用失败时抛出异常，由 API 层转为错误响应。
        """
        from app.services.xfyun_ise import XfyunIseClient, extract_pcm_from_wav

        if not audio_base64:
            raise ValueError("缺少录音数据")
        if not text_content.strip():
            raise ValueError("缺少朗读参考文本")
        if audio_format not in ("wav", "pcm"):
            raise ValueError(f"音频格式 {audio_format} 不支持评测（需 16k/16bit/单声道 wav/pcm）")
        client = XfyunIseClient()
        if not client.configured:
            raise RuntimeError("讯飞评测鉴权信息未配置（XFYUN_APP_ID/API_KEY/API_SECRET）")

        audio_bytes = base64.b64decode(audio_base64)
        pcm = audio_bytes if audio_format == "pcm" else extract_pcm_from_wav(audio_bytes)
        ise = await client.evaluate_reading(pcm, text_content, category="read_chapter")

        total = ise["total_score"]
        level = _mandarin_level(total)
        result: dict = {
            "engine": "xfyun_ise",
            "total_score": total,
            "dimension_scores": {
                "pronunciation": ise["phone_score"],
                "tone": ise["tone_score"],
                "fluency": ise["fluency_score"],
                "completeness": ise["integrity_score"],
            },
            "dimension_full_score": 100,
            "transcribed_text": "",
            "level": level,
            "is_rejected": ise["is_rejected"],
            "error_chars": ise["error_chars"],
        }

        # 基于讯飞评测数据由 LLM 生成评语与改进建议（失败则规则兜底）
        error_hint = "、".join(ise["error_chars"]) if ise["error_chars"] else "无"
        rejected_hint = "注意：检测到朗读内容与参考文本严重不符（乱读）。\n" if ise["is_rejected"] else ""
        prompt = (
            f"学生完成了一次普通话朗读测评，以下是讯飞专业语音评测引擎的结果：\n"
            f"总分：{total}/100（对应普通话等级：{level}，目标等级：{test_level}）\n"
            f"声韵（发音）得分：{ise['phone_score']}/100\n"
            f"调型（声调）得分：{ise['tone_score']}/100\n"
            f"流畅度得分：{ise['fluency_score']}/100\n"
            f"完整度得分：{ise['integrity_score']}/100\n"
            f"朗读有误的字词：{error_hint}\n"
            f"{rejected_hint}"
            f"请根据以上数据给出综合评语和针对性的改进建议。\n"
            f'请严格以JSON格式返回：{{"ai_comment": "50~120字综合评语", "suggestions": ["建议1", "建议2", "建议3"]}}'
        )
        if personality_directive:
            # 用户自定义微调：性格/说话风格对评语生效
            prompt = f"{personality_directive}\n\n{prompt}"
        comment_data = await self._call_llm(prompt)
        if isinstance(comment_data, dict) and comment_data.get("ai_comment"):
            result["ai_comment"] = comment_data["ai_comment"]
            suggestions = comment_data.get("suggestions")
            result["suggestions"] = suggestions if isinstance(suggestions, list) else []
        else:
            # LLM 不可用时的规则兜底评语
            result["ai_comment"] = (
                f"本次朗读总分{total}分，达到{level}水平。"
                + (f"以下字词发音有误：{error_hint}，建议加强练习。" if ise["error_chars"] else "发音整体较为准确，继续保持。")
            )
            result["suggestions"] = []
        return result

    # ============ 工具方法 ============

    async def _call_llm(self, prompt: str, max_tokens: int = 1500) -> dict:
        """调用 LLM 并解析 JSON 响应（失败自动重试 1 次）。

        LLM 服务存在偶发失败（网络抖动、5xx、返回非 JSON），
        直接失败会让用户看到"生成失败"甚至长时间无响应，
        重试一次可将成功率提升到接近 100%，代价仅多等 1 秒。
        """
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=0.6,
                    response_format={"type": "json_object"},
                    timeout=120,
                )
                content = response.choices[0].message.content or "{}"
                data = json.loads(content)
                if isinstance(data, dict):
                    return data
                last_error = ValueError("LLM 返回内容不是 JSON 对象")
            except Exception as e:
                last_error = e
                logger.warning("口语服务LLM调用失败(第 %d 次): %s", attempt + 1, e)
                if attempt == 0:
                    await asyncio.sleep(1)
        logger.error("口语服务LLM调用最终失败: %s", last_error)
        return {"error": str(last_error)}
