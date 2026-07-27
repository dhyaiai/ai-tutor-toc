import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from app.api.v1 import auth, assignments, questions, analytics, error_questions, ai_tutor, ai_questions, conversations, personality, compositions, oral_assessments
from app.core.config import get_settings
from app.db.session import engine
from app.db.base import Base

settings = get_settings()


def _ensure_secret_key():
    """Ensure SECRET_KEY is set. Auto-generate in dev mode, refuse startup in production."""
    if settings.SECRET_KEY and settings.SECRET_KEY != "dev-secret-key-change-in-production":
        return
    if settings.DEV_MODE:
        generated = secrets.token_urlsafe(32)
        settings.SECRET_KEY = generated
        print("[startup] DEV_MODE: auto-generated SECRET_KEY", flush=True)
    else:
        raise RuntimeError(
            "SECRET_KEY is not configured. Set it in .env or environment variable. "
            "Do NOT use the default dev key in production."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: validate critical config
    _ensure_secret_key()

    # Create tables (auto-create for SQLite, no-op for existing MySQL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Auto-migrate: add new columns that may not exist yet
        await _auto_migrate(conn)

    yield
    await engine.dispose()


async def _auto_migrate(conn):
    """Add missing columns to existing tables (safe, idempotent)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    migrations = [
        # assignment_questions: question_type (added 2024-06)
        "ALTER TABLE assignment_questions ADD COLUMN question_type VARCHAR(64) NULL",
        # assignment_questions: common_mistakes (added 2026-06)
        "ALTER TABLE assignment_questions ADD COLUMN common_mistakes JSON NULL",
        # 大题套小题：父子层级 (added 2026-06)
        "ALTER TABLE assignment_questions ADD COLUMN parent_id INT NULL",
        "ALTER TABLE assignment_questions ADD COLUMN sub_question_index INT NULL",
        "ALTER TABLE assignment_questions ADD INDEX ix_question_parent_id (parent_id)",
        "ALTER TABLE assignment_questions ADD CONSTRAINT fk_question_parent FOREIGN KEY (parent_id) REFERENCES assignment_questions(id) ON DELETE CASCADE",
        # 答案图片与人工审核备注 (added 2026-06)
        "ALTER TABLE assignment_questions ADD COLUMN answer_image_url VARCHAR(512) NULL",
        "ALTER TABLE assignment_questions ADD COLUMN manual_review_note TEXT NULL",
        # ai_generated_questions: 大题分组字段 (added 2026-06)
        "ALTER TABLE ai_generated_questions ADD COLUMN group_id VARCHAR(36) NULL",
        "ALTER TABLE ai_generated_questions ADD COLUMN sub_question_index INT NULL",
        "ALTER TABLE ai_generated_questions ADD COLUMN question_context TEXT NULL",
        "ALTER TABLE ai_generated_questions ADD INDEX ix_ai_q_group_id (group_id)",
        # ai_generated_questions: 完整解析字段 (added 2026-07)
        "ALTER TABLE ai_generated_questions ADD COLUMN analysis TEXT NULL",
        # 会话管理 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS conversations ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  title VARCHAR(128) NOT NULL DEFAULT '新对话',"
        "  subject VARCHAR(32) NULL,"
        "  status SMALLINT NOT NULL DEFAULT 1,"
        "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "  INDEX ix_conversations_user_id (user_id),"
        "  CONSTRAINT fk_conversations_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        "CREATE TABLE IF NOT EXISTS conversation_messages ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  conversation_id INT NOT NULL,"
        "  role VARCHAR(16) NOT NULL,"
        "  content TEXT NOT NULL,"
        "  reasoning TEXT NULL,"
        "  tool_calls JSON NULL,"
        "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_conversation_messages_conversation_id (conversation_id),"
        "  CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE"
        ")",
        # 助教性格配置 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS agent_personality ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL UNIQUE,"
        "  template_name VARCHAR(32) NOT NULL DEFAULT '严谨专业型',"
        "  personality_type VARCHAR(32) NOT NULL DEFAULT '严谨专业型',"
        "  speaking_style VARCHAR(32) NOT NULL DEFAULT '书面化正式',"
        "  voice_tone VARCHAR(32) NOT NULL DEFAULT '沉稳男声',"
        "  strict_level INT NOT NULL DEFAULT 3,"
        "  update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "  CONSTRAINT fk_personality_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 作文批改 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS composition_corrections ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  session_id INT NULL,"
        "  subject VARCHAR(16) NOT NULL,"
        "  title VARCHAR(255) NOT NULL DEFAULT '未命名作文',"
        "  total_score INT NOT NULL DEFAULT 0,"
        "  full_score INT NOT NULL DEFAULT 60,"
        "  content TEXT NOT NULL,"
        "  requirement TEXT NULL,"
        "  grade VARCHAR(32) NULL,"
        "  dimension_scores JSON NULL,"
        "  revision_suggestions JSON NULL,"
        "  overall_comment TEXT NULL,"
        "  polish_advice TEXT NULL,"
        "  sample_essay TEXT NULL,"
        "  strict_level INT NOT NULL DEFAULT 3,"
        "  pdf_url VARCHAR(512) NULL,"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_composition_user_id (user_id),"
        "  CONSTRAINT fk_composition_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 作文批改 - essay_type 列 (added 2026-07)
        "ALTER TABLE composition_corrections ADD COLUMN essay_type VARCHAR(32) NULL COMMENT '作文类型：读后续写/应用文/议论文等'",
        # 口语测评 - 听力测试 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS listening_tests ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  question_type VARCHAR(32) NOT NULL,"
        "  difficulty VARCHAR(16) DEFAULT '中等',"
        "  question_count INT DEFAULT 5,"
        "  total_score FLOAT DEFAULT 0,"
        "  user_score FLOAT DEFAULT 0,"
        "  strict_level INT DEFAULT 3,"
        "  grade VARCHAR(32) NULL,"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_listening_user_id (user_id),"
        "  CONSTRAINT fk_listening_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        "CREATE TABLE IF NOT EXISTS dictation_tasks ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  word_scope VARCHAR(128) NOT NULL,"
        "  word_count INT DEFAULT 10,"
        "  correct_count INT DEFAULT 0,"
        "  strict_level INT DEFAULT 3,"
        "  play_speed VARCHAR(16) DEFAULT '正常',"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_dictation_user_id (user_id),"
        "  CONSTRAINT fk_dictation_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        "CREATE TABLE IF NOT EXISTS mandarin_test_records ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  test_level VARCHAR(16) NOT NULL,"
        "  test_part VARCHAR(32) NULL,"
        "  total_score FLOAT DEFAULT 0,"
        "  dimension_scores TEXT NULL,"
        "  suggestions TEXT NULL,"
        "  audio_url VARCHAR(512) NULL,"
        "  strict_level INT DEFAULT 3,"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_mandarin_user_id (user_id),"
        "  CONSTRAINT fk_mandarin_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 口语测评作业记录（统一表）(added 2026-07)
        "CREATE TABLE IF NOT EXISTS oral_records ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  category VARCHAR(32) NOT NULL,"
        "  name VARCHAR(128) NOT NULL,"
        "  score VARCHAR(64) NULL,"
        "  grade_level VARCHAR(16) NULL COMMENT '学段：小学/初中/高中',"
        "  detail TEXT NULL,"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_oral_records_user_id (user_id),"
        "  INDEX ix_oral_records_category (category),"
        "  CONSTRAINT fk_oral_records_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 口语测评作业记录 - 补充 grade_level 列 (added 2026-07-22)
        # 注意：此 ALTER 必须在 CREATE TABLE 之后，否则表不存在时 ALTER 会失败
        "ALTER TABLE oral_records ADD COLUMN grade_level VARCHAR(16) NULL COMMENT '学段：小学/初中/高中'",
        # 知识状态追踪 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS user_knowledge_state ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  subject VARCHAR(32) NOT NULL DEFAULT '通用',"
        "  point_name VARCHAR(128) NOT NULL,"
        "  mastery_score INT NOT NULL DEFAULT 50,"
        "  mastery_level VARCHAR(16) NOT NULL DEFAULT '初步掌握',"
        "  wrong_count INT NOT NULL DEFAULT 0,"
        "  correct_count INT NOT NULL DEFAULT 0,"
        "  last_practice_time DATETIME NULL,"
        "  update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_knowledge_state_user_id (user_id),"
        "  INDEX ix_knowledge_state_subject (subject),"
        "  CONSTRAINT fk_knowledge_state_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 月份字段更名 month → usage_month (added 2026-07-22)
        "ALTER TABLE assignments CHANGE COLUMN month usage_month VARCHAR(16) NOT NULL",
        # 月份字段二次更名 applicable_month → usage_month
        "ALTER TABLE assignments CHANGE COLUMN applicable_month usage_month VARCHAR(16) NOT NULL",
    ]
    for sql in migrations:
        try:
            await conn.execute(text(sql))
            await conn.commit()
            print(f"[migrate] OK: {sql[:60]}...", flush=True)
        except OperationalError as e:
            # MySQL error 1060 = Duplicate column name — safe to ignore
            if hasattr(e, 'orig') and e.orig and hasattr(e.orig, 'args'):
                err_code = e.orig.args[0] if e.orig.args else 0
                if err_code in (1060, 1061, 1054):  # 1060=duplicate column, 1061=duplicate index, 1054=unknown column (already renamed)
                    print(f"[migrate] SKIP (already exists): {sql[:50]}...", flush=True)
                    continue
            # SQLite: "duplicate column name" in error message
            msg = str(e).lower() if e else ""
            if "duplicate column" in msg:
                print(f"[migrate] SKIP (already exists): {sql[:50]}...", flush=True)
                continue
            print(f"[migrate] WARN: {sql[:60]}... -> {e}", flush=True)
        except Exception as e:
            # Only catch "column already exists" or "table not found" — log others
            msg = str(e).lower() if e else ""
            if "already exists" in msg or "duplicate column" in msg or "no such table" in msg:
                print(f"[migrate] SKIP (safe): {sql[:50]}...", flush=True)
            else:
                print(f"[migrate] WARN: {sql[:60]}... -> {e}", flush=True)


app = FastAPI(
    title="AI Tutor",
    description="AI 助教系统 API",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:5175", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# API Routes
app.include_router(auth.router, prefix="/api/v1")
app.include_router(assignments.router, prefix="/api/v1")
app.include_router(questions.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(error_questions.router, prefix="/api/v1")
app.include_router(ai_tutor.router, prefix="/api/v1")
app.include_router(ai_questions.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(personality.router, prefix="/api/v1")
app.include_router(compositions.router, prefix="/api/v1")
app.include_router(oral_assessments.router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Dev mode: serve uploaded files from local storage
@app.get("/api/v1/files/{file_path:path}")
async def serve_local_file(file_path: str):
    """Serve uploaded files from local storage (dev mode only).

    支持容错查找：当 LLM 生成的 URL 路径不正确时（如把 reports/ 写成 corrections/），
    会根据文件名中的 UUID 模式在 reports 目录下查找匹配文件。
    """
    if not settings.DEV_MODE:
        raise HTTPException(status_code=404, detail="Not available in production mode")
    full_path = Path(settings.LOCAL_STORAGE_DIR) / file_path
    # Security: prevent path traversal
    full_path = full_path.resolve()
    storage_root = Path(settings.LOCAL_STORAGE_DIR).resolve()
    if not str(full_path).startswith(str(storage_root)):
        raise HTTPException(status_code=403, detail="Access denied")

    if not full_path.exists():
        # 容错查找：LLM 可能生成错误的目录名或文件名前缀，
        # 根据文件名中的 8 位十六进制 ID 在 reports/ 目录下模糊匹配
        import re
        filename = full_path.name
        hex_match = re.search(r"[0-9a-f]{8}", filename)
        if hex_match:
            hex_id = hex_match.group(0)
            reports_dir = storage_root / "reports"
            if reports_dir.is_dir():
                for f in reports_dir.iterdir():
                    if f.is_file() and hex_id in f.name:
                        full_path = f
                        break

        # 再次检查文件是否存在
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="File not found")

    # 报告/订正本类文件以 HTML 形式直接渲染，支持浏览器查看和打印为 PDF
    filename = full_path.name
    if "reports" in full_path.parts or full_path.suffix == ".html":
        return FileResponse(
            str(full_path),
            media_type="text/html",
            headers={"Content-Disposition": f"inline; filename={filename}"},
        )
    return FileResponse(str(full_path))
