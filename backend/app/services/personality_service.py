"""
AI 批改个性化配置加载服务

从 agent_personality 表读取用户自定义微调的三项配置：
- personality_type 性格类型
- speaking_style 说话风格
- strict_level 评分严格度（1-5）

并生成统一的批改指令文本，注入到系统内所有 AI 批改的提示词中
（作业批改、单题重分析、作文批改、普通话测评、AI 题作答评分、Agent 对话）。
"""

import logging

logger = logging.getLogger(__name__)

# 默认配置（用户未保存过配置时使用）
# voice_tone 仅影响 TTS 播报音色（male/female），不参与批改指令生成
DEFAULT_PERSONALITY: dict = {
    "personality_type": "严谨专业型",
    "speaking_style": "书面化正式",
    "voice_tone": "female",
    "strict_level": 3,
}

# 性格类型对应的行为准则
BEHAVIOR_MAP: dict[str, str] = {
    "温柔鼓励型": "语气温和亲切，多使用鼓励性词语，对学生错误保持宽容，重点标注核心错误，小失误不扣分。",
    "严谨专业型": "语气客观专业，使用书面化正式表达，按常规标准评分，错误与鼓励均衡，客观公正。",
    "幽默活泼型": "语气风趣幽默，使用口语化表达，缓解学生压力，用轻松的方式指出错误，保护学习兴趣。",
    "严格督学型": "语气简洁有力，高标准严要求，细节错误均指出，评语一针见血，推动学生不断进步。",
}


async def load_personality(db, user_id: int) -> dict:
    """加载用户的助教个性化配置，缺失或异常时返回默认配置。

    Args:
        db: AsyncSession 数据库会话
        user_id: 用户 ID

    Returns:
        {"personality_type": str, "speaking_style": str, "strict_level": int}
    """
    try:
        from sqlalchemy import select
        from app.models.personality import AgentPersonality

        result = await db.execute(
            select(AgentPersonality).where(AgentPersonality.user_id == user_id)
        )
        config = result.scalar_one_or_none()
        if not config:
            return dict(DEFAULT_PERSONALITY)
        return {
            "personality_type": config.personality_type,
            "speaking_style": config.speaking_style,
            "strict_level": config.strict_level,
        }
    except Exception as e:
        logger.warning("加载用户 %s 的助教配置失败，使用默认配置: %s", user_id, e)
        return dict(DEFAULT_PERSONALITY)


def build_grading_directive(personality: dict) -> str:
    """根据配置生成批改指令文本，追加到各批改提示词末尾。"""
    personality_type = personality.get("personality_type") or DEFAULT_PERSONALITY["personality_type"]
    speaking_style = personality.get("speaking_style") or DEFAULT_PERSONALITY["speaking_style"]
    strict_level = personality.get("strict_level") or DEFAULT_PERSONALITY["strict_level"]
    behavior = BEHAVIOR_MAP.get(personality_type, BEHAVIOR_MAP["严谨专业型"])

    return (
        f"【助教个性化设定】\n"
        f"当前助教性格：{personality_type}\n"
        f"说话风格：{speaking_style}\n"
        f"评分严格度：{strict_level}/5\n"
        f"行为准则：{behavior}\n"
        f"所有评语、分析、建议的语气和措辞必须严格贴合以上设定。\n"
        f"评分时，严格度越高打分越严、扣分越细；严格度越低越宽松、鼓励性内容越多。"
    )


async def load_grading_directive(db, user_id: int) -> str:
    """一步加载配置并生成批改指令文本（异常时返回默认配置的指令）。"""
    personality = await load_personality(db, user_id)
    return build_grading_directive(personality)
