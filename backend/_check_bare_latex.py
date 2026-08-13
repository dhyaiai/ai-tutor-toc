# 临时排查脚本：查找 answer 字段中含裸 LaTeX（有反斜杠命令但无 $ 包裹）的记录
import asyncio
from sqlalchemy import text
from app.db.session import async_session_factory

async def main():
    async with async_session_factory() as db:
        # 含反斜杠（LaTeX 命令特征）但完全不含 $
        rows = (await db.execute(text(
            "SELECT id, group_id, sub_question_index, answer, question_type "
            "FROM ai_generated_questions "
            "WHERE answer LIKE CONCAT('%', CHAR(92), '%') "
            "AND answer NOT LIKE CONCAT('%', '$', '%') "
            "ORDER BY id DESC LIMIT 50"
        ))).all()
        print("bare-latex answers (backslash, no $):", len(rows))
        for r in rows[:10]:
            print(r)

        # 含 $ 包裹的答案数量（对照组）
        n1 = (await db.execute(text(
            "SELECT COUNT(*) FROM ai_generated_questions "
            "WHERE answer LIKE CONCAT('%', '$', '%')"
        ))).scalar()
        n2 = (await db.execute(text(
            "SELECT COUNT(*) FROM ai_generated_questions"
        ))).scalar()
        print(f"answers with $: {n1}, total answers: {n2}")

asyncio.run(main())
