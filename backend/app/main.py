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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables (auto-create for SQLite, no-op for existing MySQL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Auto-migrate: add new columns that may not exist yet
        await _auto_migrate(conn)

    yield
    await engine.dispose()


async def _auto_migrate(conn):
    """Add missing columns to existing tables (safe, idempotent)."""
    from sqlalchemy import text
    migrations = [
        # assignment_questions: question_type (added 2024-06)
        "ALTER TABLE assignment_questions ADD COLUMN question_type VARCHAR(64) NULL",
        # assignment_questions: common_mistakes (added 2026-06)
        "ALTER TABLE assignment_questions ADD COLUMN common_mistakes JSON NULL",
    ]
    for sql in migrations:
        try:
            await conn.execute(text(sql))
            await conn.commit()
            print(f"[migrate] OK: {sql[:60]}...", flush=True)
        except Exception:
            # Column already exists or table doesn't exist — safe to ignore
            pass


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
