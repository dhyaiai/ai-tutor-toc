from celery import Celery
from celery.signals import task_postrun, worker_process_shutdown
from app.core.config import get_settings
from app.services.llm_usage_tracker import install_llm_usage_tracker, flush_pending_sync

settings = get_settings()

# Celery worker 进程同样需要记录 LLM Token 用量（数据看板数据源）
install_llm_usage_tracker()


# Celery 任务各自通过 asyncio.run 跑独立事件循环，任务内调度的延迟 flush 会
# 随循环关闭被取消；注册信号在"每个任务结束"与"worker 进程退出"时同步兜底
# 落库，避免用量记录滞留缓冲区甚至丢失。
def _flush_usage_boundary_sync(*args, **kwargs) -> None:
    try:
        flush_pending_sync()
    except Exception:
        pass


task_postrun.connect(_flush_usage_boundary_sync, weak=False)
worker_process_shutdown.connect(_flush_usage_boundary_sync, weak=False)

celery_app = Celery(
    "ai_tutor",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.analysis_tasks",
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
