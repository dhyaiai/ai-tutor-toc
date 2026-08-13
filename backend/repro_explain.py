"""复现"助教讲解"500:自签 JWT 走真实 HTTP 调用 /api/v1/ai-tutor/explain。"""
import asyncio
import sys
import time

sys.path.insert(0, r"d:\AICoding\AI_zhujiao\backend")


async def main():
    import httpx

    import app.db.base  # noqa: F401
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.core.security import create_access_token
    from app.db.session import async_session_factory
    from app.models.assignment import Assignment
    from app.models.question import Question
    from app.models.user import User

    settings = get_settings()

    async with async_session_factory() as db:
        # 找一个已完成分析、带图且有属主的题目
        q = (
            await db.execute(
                select(Question)
                .where(Question.image_url.isnot(None))
                .limit(20)
            )
        ).scalars().all()
        target = None
        for item in q:
            assignment = await db.get(Assignment, item.assignment_id)
            if assignment is not None:
                target = (item, assignment)
                break
        if target is None:
            print("无符合条件的题目")
            return
        question, assignment = target
        user = await db.get(User, assignment.creator_id)
        print("题目 id:", question.id, "| 属主 user:", user.phone if user else None,
              "| token_version:", user.token_version if user else None)

        if user is None:
            print("题目无属主")
            return

        token = create_access_token(user.id, settings, user.token_version or 0)

        # 模拟前端请求(题干文本 + 题目 id)
        exercise_content = (
            f"题目:{question.question_text or '(无题干文本)'}\n"
            f"学生答案:{question.student_answer or ''}\n"
            f"正确答案:{question.correct_answer or ''}"
        )
        payload = {
            "exercise_content": exercise_content,
            "subject": "语文",
            "explanation_style": "直接讲解式",
            "strict_level": 3,
            "question_id": question.id,
        }
        url = "http://127.0.0.1:8000/api/v1/ai-tutor/explain"
        headers = {"Authorization": f"Bearer {token}"}
        # 场景A:不带 question_id(纯文本 deepseek, 1 秒级)
        async with httpx.AsyncClient(timeout=60) as client:
            t0 = time.monotonic()
            resp = await client.post(url, json={
                "exercise_content": "测试题目:2+2=? 请讲解",
                "subject": "数学",
                "explanation_style": "直接讲解式",
                "strict_level": 3,
            }, headers=headers)
            print(f"场景A(纯文本) 耗时 {time.monotonic()-t0:.1f}s 状态 {resp.status_code} 响应: {resp.text[:200]}")

        # 场景B:带 question_id(多模态 VISION)
        async with httpx.AsyncClient(timeout=250) as client:
            t0 = time.monotonic()
            resp = await client.post(url, json=payload, headers=headers)
            print(f"场景B(多模态) 耗时 {time.monotonic()-t0:.1f}s 状态 {resp.status_code} 响应: {resp.text[:300]}")

asyncio.run(main())
