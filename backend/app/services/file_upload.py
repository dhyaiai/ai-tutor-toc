import asyncio
import logging
import os
import shutil
import uuid
from io import BytesIO
from pathlib import Path
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Lazy-loaded MinIO HTTP client (only created when MinIO is actually used)
_http_client = None


def _get_http_client():
    global _http_client
    if _http_client is None:
        from urllib3 import PoolManager, Timeout
        _http_client = PoolManager(
            timeout=Timeout(connect=5.0, read=10.0),
            retries=0,
        )
    return _http_client

MIME_MAP = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


class StorageService:
    """Unified storage: MinIO (production) or local filesystem (dev mode)."""

    def __init__(self):
        settings = get_settings()
        self.settings = settings
        self._client = None
        self.public_endpoint = settings.MINIO_PUBLIC_ENDPOINT
        self.bucket = settings.MINIO_BUCKET
        self.dev_mode = settings.DEV_MODE
        self.local_dir = Path(settings.LOCAL_STORAGE_DIR)

        if self.dev_mode:
            self.local_dir.mkdir(parents=True, exist_ok=True)
            (self.local_dir / "originals").mkdir(exist_ok=True)
            (self.local_dir / "questions").mkdir(exist_ok=True)
            logger.info("Storage: local filesystem mode (dir=%s)", self.local_dir)

    @property
    def client(self):
        if self._client is None and not self.dev_mode:
            from minio import Minio
            self._client = Minio(
                endpoint=self.settings.MINIO_ENDPOINT,
                access_key=self.settings.MINIO_ACCESS_KEY,
                secret_key=self.settings.MINIO_SECRET_KEY,
                secure=self.settings.MINIO_SECURE,
                http_client=_get_http_client(),
            )
        return self._client

    def _ensure_bucket(self):
        if self.dev_mode:
            return
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info("Created bucket: %s", self.bucket)
        except Exception as e:
            logger.error("Failed to ensure bucket %s: %s", self.bucket, e)
            raise

    async def _run_on_client(self, method_name: str, *args, **kwargs):
        method = getattr(self.client, method_name)
        return await asyncio.to_thread(method, *args, **kwargs)

    # ── local storage helpers ──────────────────────────────────────

    def _local_save(self, subdir: str, file_data: bytes, filename: str, user_id: int) -> str:
        """Save to local filesystem, returns relative path."""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        user_dir = self.local_dir / subdir / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        rel_path = f"{subdir}/{user_id}/{uuid.uuid4()}.{ext}"
        full_path = self.local_dir / rel_path
        full_path.write_bytes(file_data)
        logger.info("Local save: %s (%d bytes)", rel_path, len(file_data))
        return rel_path

    # ── public API ─────────────────────────────────────────────────

    async def save_original(self, file_data: bytes, filename: str, user_id: int) -> str:
        if self.dev_mode:
            return self._local_save("originals", file_data, filename, user_id)

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "pdf"
        object_name = f"originals/{user_id}/{uuid.uuid4()}.{ext}"
        content_type = MIME_MAP.get(ext, "application/octet-stream")
        self._ensure_bucket()
        await self._run_on_client(
            "put_object",
            bucket_name=self.bucket,
            object_name=object_name,
            data=BytesIO(file_data),
            length=len(file_data),
            content_type=content_type,
        )
        logger.info("MinIO save: %s (%d bytes)", object_name, len(file_data))
        return object_name

    async def save_question_image(self, image_data: bytes, user_id: int, assignment_id: int, suffix: str = "") -> str:
        if self.dev_mode:
            subdir = f"questions/{user_id}/{assignment_id}"
            (self.local_dir / subdir).mkdir(parents=True, exist_ok=True)
            rel_path = f"{subdir}/{uuid.uuid4()}{suffix}.png"
            (self.local_dir / rel_path).write_bytes(image_data)
            return rel_path

        object_name = f"questions/{user_id}/{assignment_id}/{uuid.uuid4()}{suffix}.png"
        self._ensure_bucket()
        await self._run_on_client(
            "put_object",
            bucket_name=self.bucket,
            object_name=object_name,
            data=BytesIO(image_data),
            length=len(image_data),
            content_type="image/png",
        )
        return object_name

    async def get_presigned_url(self, object_name: str, expires: int = 3600) -> str:
        if self.dev_mode:
            # In dev mode, serve files via /api/v1/files/{path} endpoint
            return f"/api/v1/files/{object_name}"

        try:
            url = await self._run_on_client(
                "presigned_get_object", self.bucket, object_name, expires=expires
            )
            if not url:
                logger.error("MinIO returned empty presigned URL for %s", object_name)
                raise RuntimeError(f"MinIO returned empty presigned URL for: {object_name}")
            internal = self.settings.MINIO_ENDPOINT
            public = self.settings.MINIO_PUBLIC_ENDPOINT
            if internal != public:
                url = url.replace(internal, public)
            return url
        except Exception:
            logger.error("Failed to generate presigned URL for %s", object_name, exc_info=True)
            raise RuntimeError(f"Storage temporarily unavailable: cannot access {object_name}")

    async def get_file_bytes(self, object_name: str) -> bytes | None:
        """Download file bytes from storage (used by analysis pipeline)."""
        if self.dev_mode:
            full_path = self.local_dir / object_name
            if full_path.exists():
                return full_path.read_bytes()
            return None

        try:
            response = await self._run_on_client(
                "get_object", self.bucket, object_name
            )
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except Exception:
            logger.warning("Failed to download %s", object_name, exc_info=True)
            return None

    async def delete_object(self, object_name: str):
        if self.dev_mode:
            full_path = self.local_dir / object_name
            if full_path.exists():
                full_path.unlink()
            return

        try:
            await self._run_on_client("remove_object", self.bucket, object_name)
        except Exception:
            logger.warning("Failed to delete object: %s", object_name, exc_info=True)
