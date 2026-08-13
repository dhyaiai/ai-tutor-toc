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

    async def save_report(self, file_data: bytes, filename: str, user_id: int) -> str:
        """保存报告类文件（学情报告/订正本/学习计划，PDF 或 HTML），返回存储标识。

        dev 模式返回本地相对路径（配合 /api/v1/files/ 访问）；
        生产模式存入 MinIO reports/{user_id}/ 前缀，返回 object_name
        （配合 get_presigned_url 生成可访问的预签名 URL）。
        """
        if self.dev_mode:
            subdir = f"reports/{user_id}"
            (self.local_dir / subdir).mkdir(parents=True, exist_ok=True)
            rel_path = f"{subdir}/{filename}"
            (self.local_dir / rel_path).write_bytes(file_data)
            return rel_path

        object_name = f"reports/{user_id}/{filename}"
        content_type = (
            "application/pdf"
            if filename.lower().endswith(".pdf")
            else "text/html; charset=utf-8"
        )
        self._ensure_bucket()
        await self._run_on_client(
            "put_object",
            bucket_name=self.bucket,
            object_name=object_name,
            data=BytesIO(file_data),
            length=len(file_data),
            content_type=content_type,
        )
        logger.info("MinIO save report: %s (%d bytes)", object_name, len(file_data))
        return object_name

    async def get_presigned_url(self, object_name: str, expires: int = 3600) -> str:
        if self.dev_mode:
            # In dev mode, serve files via /api/v1/files/{path} endpoint
            return f"/api/v1/files/{object_name}"

        # 生产模式：校验 MINIO_PUBLIC_ENDPOINT 必须配置且不为内网地址
        if not self.settings.MINIO_PUBLIC_ENDPOINT or self.settings.MINIO_PUBLIC_ENDPOINT == "localhost:9000":
            logger.error("MINIO_PUBLIC_ENDPOINT 未正确配置，生产环境必须设置公网可访问的 MinIO 地址")
            raise RuntimeError("MINIO_PUBLIC_ENDPOINT 未配置：生产环境必须设置公网可访问的 MinIO 地址")

        try:
            url = await self._run_on_client(
                "presigned_get_object", self.bucket, object_name, expires=expires
            )
            if not url:
                logger.error("MinIO returned empty presigned URL for %s", object_name)
                raise RuntimeError(f"MinIO returned empty presigned URL for: {object_name}")
            
            # 强制使用公网端点，防止内网地址泄露
            public = self.settings.MINIO_PUBLIC_ENDPOINT
            # 确保 URL 使用正确的 scheme（http/https）
            if public.startswith("https://"):
                url = url.replace("http://", "https://")
            # 替换主机部分
            from urllib.parse import urlparse, urlunparse
            parsed = urlparse(url)
            parsed_public = urlparse(f"http://{public}")  # 加 scheme 以便解析
            # 重建 URL：使用公网主机，保持路径和查询参数
            new_parsed = parsed._replace(
                scheme=parsed_public.scheme or parsed.scheme,
                netloc=parsed_public.netloc
            )
            return urlunparse(new_parsed)
        except Exception:
            logger.error("Failed to generate presigned URL for %s", object_name, exc_info=True)
            raise RuntimeError(f"Storage temporarily unavailable: cannot access {object_name}")

    async def get_file_bytes(self, object_name: str) -> bytes | None:
        """Download file bytes from storage (used by analysis pipeline)."""
        if self.dev_mode:
            # 路径穿越兜底防护：即使个别调用点漏了归属校验，本地直读也必须
            # resolve 后确认仍在存储根目录内（拒绝 `..`/绝对路径/符号链接越界读取）
            full_path = (self.local_dir / object_name).resolve()
            if not full_path.is_relative_to(self.local_dir.resolve()):
                logger.warning("拒绝越界读取文件: %s", object_name)
                return None
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

    async def save_file(self, object_name: str, file_data: bytes) -> str:
        """通用存储方法：将文件保存到指定路径（本地或 MinIO）。

        与 save_original/save_question_image 不同，本方法接受完整的 object_name
        作为存储路径，不修改路径或文件名。适用于答案文件等非标准前缀的存储需求。
        """
        if self.dev_mode:
            # 路径穿越防护：resolve 后确认目标路径仍在存储根目录内
            full_path = (self.local_dir / object_name).resolve()
            if not full_path.is_relative_to(self.local_dir.resolve()):
                logger.warning("拒绝越界写入文件: %s", object_name)
                raise ValueError(f"非法文件路径: {object_name}")
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(file_data)
            logger.info("Local save: %s (%d bytes)", object_name, len(file_data))
            return object_name

        content_type = MIME_MAP.get(object_name.rsplit(".", 1)[-1].lower(), "application/octet-stream")
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

    # ── 用户数据清理 ──────────────────────────────────────────────

    async def delete_user_storage(self, user_id: int):
        """删除指定用户在对象存储/本地磁盘上的全部文件（删除用户时调用）。

        存储布局约定：各前缀下按 {prefix}/{user_id}/ 分目录存放（见各 save_* 方法），
        删除用户时把该用户在所有前缀下的目录整批清空，避免隐私数据残留与存储泄漏。
        仅作尽力清理：失败只记日志，不阻断用户删除流程。
        """
        # 与各保存点前缀保持一致（originals/questions/reports/answers/oral_audio/editor）
        prefixes = ("originals", "questions", "reports", "answers", "oral_audio", "editor")

        if self.dev_mode:
            for prefix in prefixes:
                user_dir = (self.local_dir / prefix / str(user_id)).resolve()
                # 路径穿越防护：确认 user_dir 仍在存储根目录内
                if not user_dir.is_relative_to(self.local_dir.resolve()):
                    logger.warning("拒绝越界删除用户目录: %s", user_dir)
                    continue
                if user_dir.exists():
                    shutil.rmtree(user_dir, ignore_errors=True)
                    logger.info("Local cleanup user storage: %s", user_dir)
            return

        try:
            for prefix in prefixes:
                object_prefix = f"{prefix}/{user_id}/"
                try:
                    objects = list(
                        self.client.list_objects(
                            self.bucket, prefix=object_prefix, recursive=True
                        )
                    )
                except Exception:
                    # 前缀对象可能不存在，跳过
                    continue
                if objects:
                    errors = self.client.remove_objects(self.bucket, objects)
                    for err in errors:
                        if err:
                            logger.warning(
                                "清理对象失败 %s: %s", err.object_name, err
                            )
                    logger.info("MinIO cleanup user %d storage under %s (%d objects)",
                                user_id, object_prefix, len(objects))
        except Exception as e:
            logger.warning("清理用户 %d 存储失败: %s", user_id, e)
