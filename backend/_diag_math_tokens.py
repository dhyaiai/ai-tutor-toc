# 临时诊断脚本：查看含 @@MATH{ 标记的题目在数据库中的实际存储内容
# 只输出题目内容字段，不输出任何数据库凭据
import re, sys
from urllib.parse import urlsplit

# 读取 .env 中的 DATABASE_URL（不打印）
db_url = None
try:
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                db_url = line.split("=", 1)[1].strip()
                break
except Exception as e:
    print("read .env failed:", e)
    sys.exit(1)

if not db_url:
    print("no DATABASE_URL in .env")
    sys.exit(1)

parts = urlsplit(db_url.replace("mysql+aiomysql://", "mysql://"))
host, port = parts.hostname, parts.port or 3306
db = parts.path.lstrip("/")

import pymysql

conn = pymysql.connect(host=host, port=port, user=parts.username, password=parts.password,
                       database=db, charset="utf8mb4")
cur = conn.cursor()

for table, cols, like_cols in [
    ("assignment_questions", "id, question_text, correct_answer, analysis_detail", ["question_text", "correct_answer", "analysis_detail"]),
    ("ai_generated_questions", "id, question_text, answer, analysis", ["question_text", "answer", "analysis"]),
]:
    cond = " OR ".join([c + " LIKE '%@@MATH{%'" for c in like_cols])
    if "--tricone" not in sys.argv:
        cond = " OR ".join([c + " LIKE '%@@MATH{%'" for c in like_cols])
    try:
        if "--tricone" in sys.argv:
            cur.execute("SELECT " + cols + " FROM " + table +
                        " WHERE question_text LIKE '%三棱锥%' OR correct_answer LIKE '%三棱锥%' LIMIT 5")
        else:
            cur.execute("SELECT " + cols + " FROM " + table + " WHERE " + cond + " LIMIT 5")
        rows = cur.fetchall()
    except Exception as e:
        print(table, "query error:", e)
        continue
    if not rows:
        print(f"== {table}: 无含 @@MATH{{ 的记录 ==")
        continue
    print(f"== {table}: 含 @@MATH{{ 的记录 {len(rows)} 条 ==")
    for r in rows:
        print("---")
        for c, v in zip(cols.split(", "), r):
            if isinstance(v, str) and len(v) > 300:
                v = v[:300] + "…"
            print(f"  {c}: {v!r}")

cur.close()
conn.close()
