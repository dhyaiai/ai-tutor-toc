from celery import Celery
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ai_tutor",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.analysis_tasks",
        "app.tasks.vector_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_soft_time_limit=300,
    task_time_limit=360,
    task_default_retry_delay=60,
    task_max_retries=3,
)
