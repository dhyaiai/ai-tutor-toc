"""
口语测评 API

3 个子模块：
- POST /oral/listening/generate  — 生成听力试卷
- POST /oral/listening/submit   — 提交听力答案
- POST /oral/dictation/generate — 生成听写任务
- POST /oral/dictation/submit   — 提交听写结果
- POST /oral/mandarin/evaluate  — 普通话测评
- GET  /oral/tts                — 文本转语音（Edge TTS，返回 MP3 流）
"""

import base64
import json
import logging
import io
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.oral_assessment import OralRecord
from app.services.oral_service import OralService
from app.services.knowledge_tracker import KnowledgeTracker
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/oral", tags=["oral"])

service = OralService()


async def _create_oral_record(
    db: AsyncSession, user_id: int, category: str, score: str, detail: dict,
    grade_level: str = "",
) -> dict:
    """创建一条口语测评作业记录。

    名称 = 类别 + 年月日（如"英语听力20260720"）；同一天多次则在名称后面加序号（如"...20260720(2)"）。
    """
    today = date.today()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=1)
    count_stmt = (
        select(func.count())
        .select_from(OralRecord)
        .where(
            OralRecord.user_id == user_id,
            OralRecord.category == category,
            OralRecord.create_time >= start,
            OralRecord.create_time < end,
        )
    )
    same_day_count = (await db.execute(count_stmt)).scalar() or 0
    seq = same_day_count + 1
    date_str = today.strftime("%Y%m%d")
    name = f"{category}{date_str}" if seq == 1 else f"{category}{date_str}({seq})"

    record = OralRecord(
        user_id=user_id,
        category=category,
        name=name,
        score=score,
        grade_level=grade_level or None,
        detail=json.dumps(detail, ensure_ascii=False) if detail else None,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {
        "id": record.id,
        "category": record.category,
        "name": record.name,
        "score": record.score,
        "grade_level": record.grade_level,
        "created_at": record.create_time.isoformat(),
    }


# ============ 请求模型 ============

class GenerateListeningRequest(BaseModel):
    question_type: str = Field(default="短对话")
    difficulty: str = Field(default="中等")
    question_count: int = Field(default=5)
    grade: str | None = None
    grade_level: str | None = Field(default=None, description="学段：小学/初中/高中")


class SubmitListeningRequest(BaseModel):
    questions: list[dict]
    answers: list[str]
    transcript: str = ""          # 听力原文（整段对话/短文）
    question_type: str = "短对话"
    difficulty: str = "中等"
    grade_level: str = Field(default="", description="学段：小学/初中/高中")


class GenerateDictationRequest(BaseModel):
    word_scope: str
    word_count: int = Field(default=10)
    direction: str = Field(default="汉译英", description="测试方向：汉译英/英译汉/默写单词/中英混合")
    difficulty: str = Field(default="中等", description="难度：简单/中等/困难")


class SubmitDictationRequest(BaseModel):
    words: list[dict]
    user_spellings: list[str]
    direction: str = Field(default="汉译英")
    difficulty: str = Field(default="中等")
    broadcast_text: str = ""
    word_scope: str = ""


class MandarinEvaluateRequest(BaseModel):
    test_level: str = Field(default="二级甲等")
    test_part: str | None = None
    text_content: str = ""
    strict_level: int = Field(default=3)
    evaluation_mode: str = Field(default="text", description="评测模式：ai_generated / free_speech / text")


class GenerateMandarinTextRequest(BaseModel):
    topic: str = Field(default="", description="话题，留空则随机")
    difficulty: str = Field(default="中等", description="难度：简单/中等/困难")
    length: str = Field(default="短", description="文本长度：短/中/长")


def _save_audio_file(audio_bytes: bytes, filename: str, user_id: int) -> str:
    """保存音频文件到本地存储，返回相对路径。"""
    settings = get_settings()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "webm"
    if ext not in ("webm", "wav", "mp3", "ogg", "m4a", "aac"):
        ext = "webm"
    user_dir = Path(settings.LOCAL_STORAGE_DIR) / "oral_audio" / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    import uuid
    rel_path = f"oral_audio/{user_id}/{uuid.uuid4()}.{ext}"
    full_path = Path(settings.LOCAL_STORAGE_DIR) / rel_path
    full_path.write_bytes(audio_bytes)
    logger.info("音频文件已保存: %s (%d bytes)", rel_path, len(audio_bytes))
    return rel_path


# ============ 英语听力 ============

@router.post("/listening/generate")
async def generate_listening(
    req: GenerateListeningRequest,
    current_user: User = Depends(get_current_user),
):
    """生成英语听力试卷"""
    result = await service.generate_listening_test(
        question_type=req.question_type,
        difficulty=req.difficulty,
        question_count=req.question_count,
        grade=req.grade,
        grade_level=req.grade_level,
    )
    return result


@router.post("/listening/submit")
async def submit_listening(
    req: SubmitListeningRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交听力答案并批改"""
    result = await service.submit_listening_answers(req.questions, req.answers)

    # 更新知识状态
    try:
        tracker = KnowledgeTracker(db)
        await tracker.update(
            user_id=current_user.id,
            knowledge_points=[{
                "point_name": "英语听力理解",
                "subject": "英语",
                "mastery_change": 1 if result["correct_rate"] >= 0.7 else -1,
                "behavior_type": "口语正确" if result["correct_rate"] >= 0.7 else "口语错误",
            }],
            update_source="口语测评",
        )
    except Exception:
        pass

    # 生成作业记录：完整保存听力原文、逐题题目/作答/正确答案/对错（含每题分值）
    try:
        record = await _create_oral_record(
            db, current_user.id, "英语听力",
            score=f"{result.get('total_score', 0)}/{result.get('full_score', 0)}",
            grade_level=req.grade_level or "",
            detail={
                "transcript": req.transcript,
                "question_type": req.question_type,
                "difficulty": req.difficulty,
                "grade_level": req.grade_level or "",
                "correct_rate": result.get("correct_rate"),
                "grade": result.get("grade"),
                "total_score": result.get("total_score"),
                "full_score": result.get("full_score"),
                "details": result.get("details", []),
            },
        )
        result["record"] = record
    except Exception:
        logger.exception("创建英语听力作业记录失败")

    return result


# ============ 单词听写 ============

@router.post("/dictation/generate")
async def generate_dictation(
    req: GenerateDictationRequest,
    current_user: User = Depends(get_current_user),
):
    """生成单词听写任务（AI 生成老师口语化播报文本 + 答案词表）"""
    return await service.generate_dictation_task(
        word_scope=req.word_scope,
        word_count=req.word_count,
        direction=req.direction,
        difficulty=req.difficulty,
    )


async def _after_dictation_submit(
    db: AsyncSession, user_id: int, result: dict, direction: str,
    broadcast_text: str, word_scope: str, answer_mode: str, difficulty: str = "中等",
) -> None:
    """听写提交后的知识状态更新与作业记录创建（键盘/图片两种作答共用）。"""
    try:
        tracker = KnowledgeTracker(db)
        for w in result.get("wrong_words", []):
            await tracker.update(
                user_id=user_id,
                knowledge_points=[{
                    "point_name": f"单词拼写-{w.get('word', '')}",
                    "subject": "英语",
                    "mastery_change": -1,
                    "behavior_type": "口语错误",
                }],
                update_source="口语测评",
            )
    except Exception:
        pass

    try:
        record = await _create_oral_record(
            db, user_id, "单词听写",
            score=f"{result.get('correct_count', 0)}/{result.get('total', 0)}",
            detail={
                "wrong_count": result.get("wrong_count"),
                "direction": direction,
                "difficulty": difficulty,
                "broadcast_text": broadcast_text,
                "word_scope": word_scope,
                "answer_mode": answer_mode,
                "wrong_words": result.get("wrong_words", []),
                "details": result.get("details", []),
            },
        )
        result["record"] = record
    except Exception:
        logger.exception("创建单词听写作业记录失败")


@router.post("/dictation/submit")
async def submit_dictation(
    req: SubmitDictationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交听写结果并批改（键盘输入，按行匹配）"""
    result = await service.submit_dictation_result(req.words, req.user_spellings, req.direction)
    await _after_dictation_submit(
        db, current_user.id, result, req.direction,
        req.broadcast_text, req.word_scope, "keyboard", req.difficulty,
    )
    return result


@router.post("/dictation/submit-image")
async def submit_dictation_image(
    words: str = Form(...),
    direction: str = Form(default="汉译英"),
    difficulty: str = Form(default="中等"),
    broadcast_text: str = Form(default=""),
    word_scope: str = Form(default=""),
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交听写结果并批改（上传手写作答图片，多模态AI识别批改）"""
    try:
        words_list = json.loads(words) if words else []
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="words 参数必须为合法 JSON")
    if not isinstance(words_list, list) or not words_list:
        raise HTTPException(status_code=400, detail="缺少听写答案词表")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="上传的图片为空")
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = "image/jpeg" if image_bytes[:3] == b"\xff\xd8\xff" else "image/png"

    result = await service.grade_dictation_image(words_list, b64, mime)
    await _after_dictation_submit(
        db, current_user.id, result, direction,
        broadcast_text, word_scope, "upload", difficulty,
    )
    return result


# ============ 普通话测评 ============

@router.post("/mandarin/generate-text")
async def generate_mandarin_text(
    req: GenerateMandarinTextRequest,
    current_user: User = Depends(get_current_user),
):
    """生成普通话朗读文本（AI生成文本模式）。

    返回一段适合朗读测评的中文文本，包含话题、难度标注和拼音提示。
    """
    result = await service.generate_mandarin_text(
        topic=req.topic,
        difficulty=req.difficulty,
        length=req.length,
    )
    return result


@router.post("/mandarin/evaluate")
async def evaluate_mandarin(
    test_level: str = Form(default="二级甲等"),
    test_part: str | None = Form(default=None),
    text_content: str = Form(default=""),
    strict_level: int = Form(default=3),
    evaluation_mode: str = Form(default="text"),
    audio: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """普通话水平测试评分。

    支持两种模式：
    - 音频评测（evaluation_mode=ai_generated 或 free_speech）：上传录音文件，LLM 进行语音识别+评测
    - 纯文本评测（evaluation_mode=text，旧版兼容）：仅根据文本内容评测

    音频评测返回：transcribed_text（转写文本）、ai_comment（AI评语）、各维度得分
    """
    audio_base64 = ""
    audio_format = "webm"
    audio_path = ""

    # 处理音频上传
    if audio is not None:
        audio_bytes = await audio.read()
        if len(audio_bytes) > 0:
            # 保存音频文件
            audio_path = _save_audio_file(
                audio_bytes,
                audio.filename or "recording.webm",
                current_user.id,
            )
            # 编码为 base64 发送给 LLM
            audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
            audio_format = (audio.filename or "webm").rsplit(".", 1)[-1].lower()

    # 读取用户的助教个性化配置，评分严格度以全局配置为准（覆盖前端传入值）
    from app.services.personality_service import load_personality, build_grading_directive
    personality = await load_personality(db, current_user.id)
    personality_directive = build_grading_directive(personality)

    # 调用评测服务
    result = await service.evaluate_mandarin(
        test_level=test_level,
        test_part=test_part,
        text_content=text_content,
        strict_level=personality["strict_level"],
        audio_base64=audio_base64,
        audio_format=audio_format,
        evaluation_mode=evaluation_mode,
        personality_directive=personality_directive,
    )

    # 更新知识状态
    try:
        tracker = KnowledgeTracker(db)
        await tracker.update(
            user_id=current_user.id,
            knowledge_points=[{
                "point_name": "普通话发音",
                "subject": "语文",
                "mastery_change": 1 if result.get("total_score", 0) >= 70 else -1,
                "behavior_type": "口语正确" if result.get("total_score", 0) >= 70 else "口语错误",
            }],
            update_source="口语测评",
        )
    except Exception:
        pass

    # 生成作业记录（保存完整信息：等级、音频、转写文本、AI评语）
    try:
        record = await _create_oral_record(
            db, current_user.id, "普通话测评",
            score=f"{result.get('total_score', 0)}分",
            detail={
                "test_level": test_level,
                "evaluation_mode": evaluation_mode,
                "audio_url": audio_path,
                "transcribed_text": result.get("transcribed_text", ""),
                "ai_comment": result.get("ai_comment", ""),
                "dimension_scores": result.get("dimension_scores", {}),
                "suggestions": result.get("suggestions", []),
                "total_score": result.get("total_score", 0),
                "level": result.get("level", test_level),
                "reference_text": text_content if evaluation_mode == "ai_generated" else "",
            },
        )
        result["record"] = record
    except Exception:
        logger.exception("创建普通话测评作业记录失败")

    return result


@router.post("/mandarin/evaluate-json")
async def evaluate_mandarin_json(
    req: MandarinEvaluateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """普通话水平测试评分（JSON请求体，纯文本模式，兼容旧版前端调用）。"""
    # 读取用户的助教个性化配置，评分严格度以全局配置为准（覆盖前端传入值）
    from app.services.personality_service import load_personality, build_grading_directive
    personality = await load_personality(db, current_user.id)
    result = await service.evaluate_mandarin(
        test_level=req.test_level,
        test_part=req.test_part,
        text_content=req.text_content,
        strict_level=personality["strict_level"],
        evaluation_mode="text",
        personality_directive=build_grading_directive(personality),
    )

    try:
        tracker = KnowledgeTracker(db)
        await tracker.update(
            user_id=current_user.id,
            knowledge_points=[{
                "point_name": "普通话发音",
                "subject": "语文",
                "mastery_change": 1 if result.get("total_score", 0) >= 70 else -1,
                "behavior_type": "口语正确" if result.get("total_score", 0) >= 70 else "口语错误",
            }],
            update_source="口语测评",
        )
    except Exception:
        pass

    try:
        record = await _create_oral_record(
            db, current_user.id, "普通话测评",
            score=f"{result.get('total_score', 0)}分",
            detail={
                "test_level": req.test_level,
                "evaluation_mode": "text",
                "transcribed_text": req.text_content,
                "ai_comment": result.get("suggestions", ""),
                "dimension_scores": result.get("dimension_scores", {}),
                "suggestions": result.get("suggestions", []),
                "total_score": result.get("total_score", 0),
                "level": result.get("level", req.test_level),
            },
        )
        result["record"] = record
    except Exception:
        logger.exception("创建普通话测评作业记录失败")

    return result


# ============ TTS 语音合成 ============

# 可用的英语语音列表（Edge TTS 免费提供）
_EDGE_VOICES = {
    "default": "en-US-JennyNeural",       # 美式女声
    "male": "en-US-GuyNeural",            # 美式男声
    "british": "en-GB-SoniaNeural",       # 英式女声
    "british_male": "en-GB-RyanNeural",   # 英式男声
    "mixed": "zh-CN-XiaoxiaoNeural",      # 中文女声（可同时朗读中英混合文本，用于单词听写播报）
}


@router.get("/tts")
async def text_to_speech(
    text: str = Query(..., min_length=1, max_length=3000, description="要合成的文本"),
    voice: str = Query(default="default", description="语音名称：default/male/british/british_male"),
    rate: str = Query(default="+0%", description="语速，如 '+0%'、'-20%'、'+30%'"),
):
    """使用 Microsoft Edge TTS 将文本合成为 MP3 音频流。

    edge_tts 的 stream() 直接输出 MP3 帧（已编码），无需再转换。
    相比浏览器内置 SpeechSynthesis：
    - 不依赖系统语音引擎（Windows 中文系统无需额外装英文语音包）
    - 神经网络语音，发音自然
    - 通用于所有操作系统

    前端用法：
        const audio = new Audio(`/api/v1/oral/tts?text=${encodeURIComponent(text)}`);
        await audio.play();
    """
    import edge_tts

    edge_voice = _EDGE_VOICES.get(voice, _EDGE_VOICES["default"])

    try:
        communicate = edge_tts.Communicate(text, edge_voice, rate=rate)
        audio_chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])

        if not audio_chunks:
            return StreamingResponse(io.BytesIO(b""), media_type="audio/mpeg")

        mp3_data = b"".join(audio_chunks)
        return StreamingResponse(
            io.BytesIO(mp3_data),
            media_type="audio/mpeg",
            headers={
                "Content-Length": str(len(mp3_data)),
                "Cache-Control": "public, max-age=86400",
            },
        )
    except Exception as e:
        logger.exception("Edge TTS 合成失败")
        raise HTTPException(status_code=500, detail=f"语音合成失败: {str(e)}")


@router.get("/tts-dialogue")
async def text_to_speech_dialogue(
    text: str = Query(..., min_length=1, max_length=5000, description="含 M:/W: 标签的对话文本"),
    rate: str = Query(default="+0%", description="语速，如 '+0%'、'-20%'、'+30%'"),
):
    """将带 M:/W: 标签的对话文本合成为 MP3 音频流。

    自动识别每行开头的说话人标签：
    - M: / Man: → 男声 (en-US-GuyNeural)
    - W: / Woman: → 女声 (en-US-JennyNeural)
    - 无标签行 → 默认女声

    逐行合成后拼接为单一 MP3，行间插入约 300ms 静音。
    """
    import edge_tts

    MALE_VOICE = "en-US-GuyNeural"
    FEMALE_VOICE = "en-US-JennyNeural"

    # 解析对话行为 (voice, text) 列表
    lines = text.strip().split("\n")
    segments: list[tuple[str, str]] = []  # [(voice_name, line_text), ...]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # 识别说话人标签
        if stripped.startswith("M:") or stripped.startswith("M：") or stripped.lower().startswith("man:") or stripped.lower().startswith("man："):
            voice = MALE_VOICE
            # 去掉标签，保留对话内容
            content = stripped.split(":", 1)[-1].split("：", 1)[-1].strip()
        elif stripped.startswith("W:") or stripped.startswith("W：") or stripped.lower().startswith("woman:") or stripped.lower().startswith("woman："):
            voice = FEMALE_VOICE
            content = stripped.split(":", 1)[-1].split("：", 1)[-1].strip()
        else:
            # 无标签行使用女声
            voice = FEMALE_VOICE
            content = stripped
        if content:
            segments.append((voice, content))

    if not segments:
        raise HTTPException(status_code=400, detail="对话文本中没有有效内容")

    try:
        # 逐段合成并拼接
        all_mp3_chunks: list[bytes] = []
        for voice, seg_text in segments:
            communicate = edge_tts.Communicate(seg_text, voice, rate=rate)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    all_mp3_chunks.append(chunk["data"])

        if not all_mp3_chunks:
            return StreamingResponse(io.BytesIO(b""), media_type="audio/mpeg")

        mp3_data = b"".join(all_mp3_chunks)
        return StreamingResponse(
            io.BytesIO(mp3_data),
            media_type="audio/mpeg",
            headers={
                "Content-Length": str(len(mp3_data)),
                "Cache-Control": "public, max-age=86400",
            },
        )
    except Exception as e:
        logger.exception("对话 TTS 合成失败")
        raise HTTPException(status_code=500, detail=f"对话语音合成失败: {str(e)}")


# ============ 作业记录 ============

@router.get("/records")
async def list_oral_records(
    category: str | None = None,
    grade_level: str | None = None,
    question_type: str | None = None,
    word_scope: str | None = None,
    direction: str | None = None,
    difficulty: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询当前用户的口语测评作业记录（可按类别、学段、题型、词库范围、测试方向、难度过滤）"""
    stmt = select(OralRecord).where(OralRecord.user_id == current_user.id)
    if category:
        stmt = stmt.where(OralRecord.category == category)
    if grade_level:
        stmt = stmt.where(OralRecord.grade_level == grade_level)
    stmt = stmt.order_by(OralRecord.create_time.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()

    # 从 detail JSON 中提取题型/词库范围/测试方向/难度
    records = []
    for r in rows:
        q_type = ""
        w_scope = ""
        w_direction = ""
        w_difficulty = ""
        try:
            detail = json.loads(r.detail) if r.detail else {}
            q_type = detail.get("question_type", "")
            w_scope = detail.get("word_scope", "")
            w_direction = detail.get("direction", "")
            w_difficulty = detail.get("difficulty", "")
        except Exception:
            pass

        # 题型/词库范围/测试方向/难度筛选（从 detail JSON 提取，需在 Python 层面过滤）
        if question_type and q_type != question_type:
            continue
        if word_scope and w_scope != word_scope:
            continue
        if direction and w_direction != direction:
            continue
        if difficulty and w_difficulty != difficulty:
            continue

        records.append({
            "id": r.id,
            "category": r.category,
            "name": r.name,
            "score": r.score,
            "grade_level": r.grade_level,
            "question_type": q_type,
            "word_scope": w_scope,
            "direction": w_direction,
            "difficulty": w_difficulty,
            "created_at": r.create_time.isoformat(),
        })
    return records


@router.get("/records/{record_id}")
async def get_oral_record_detail(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查看单条作业记录详情（包含听力原文/题目/作答/对错）。"""
    stmt = select(OralRecord).where(
        OralRecord.id == record_id,
        OralRecord.user_id == current_user.id,
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    try:
        detail = json.loads(record.detail) if record.detail else {}
    except Exception:
        detail = {}
    return {
        "id": record.id,
        "category": record.category,
        "name": record.name,
        "score": record.score,
        "grade_level": record.grade_level,
        "created_at": record.create_time.isoformat(),
        "detail": detail,
    }


@router.delete("/records/{record_id}")
async def delete_oral_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除一条口语测评作业记录（仅允许删除自己的记录）。"""
    stmt = select(OralRecord).where(
        OralRecord.id == record_id,
        OralRecord.user_id == current_user.id,
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在或无权操作")
    await db.delete(record)
    await db.commit()
    return {"message": "已删除", "record_id": record_id}
