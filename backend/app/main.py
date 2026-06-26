import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from app.api.v1 import auth, assignments, questions, analytics, error_questions, ai_tutor, ai_questions
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
                if err_code == 1060:
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


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Dev mode: serve uploaded files from local storage
@app.get("/api/v1/files/{file_path:path}")
async def serve_local_file(file_path: str):
    """Serve uploaded files from local storage (dev mode only)."""
    if not settings.DEV_MODE:
        raise HTTPException(status_code=404, detail="Not available in production mode")
    full_path = Path(settings.LOCAL_STORAGE_DIR) / file_path
    # Security: prevent path traversal
    full_path = full_path.resolve()
    if not str(full_path).startswith(str(Path(settings.LOCAL_STORAGE_DIR).resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(full_path))
