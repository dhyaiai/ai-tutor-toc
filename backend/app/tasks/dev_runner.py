"""
Development task runner that executes tasks synchronously without Celery/Redis.
Used when DEV_MODE=true.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)

# 持有后台任务引用：asyncio 文档明确警示，create_task 创建的任务若不保存引用，
# 可能在执行中途被垃圾回收导致任务凭空消失（表现为题目永远停在 pending）。
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _handle_task_done(task: asyncio.Task, assignment_id: int):
    """后台任务完成回调：清理引用 + 记录异常。"""
    _BACKGROUND_TASKS.discard(task)
    # 检查异常：后台任务异常会被静默吞掉，用户看到的作业状态永远是 grading
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning("[dev] 作业 %d 的分析任务被取消", assignment_id)
    except Exception as e:
        logger.error(
            "[dev] 作业 %d 的分析任务异常终止: %s",
            assignment_id, e, exc_info=True,
        )


def _run_in_thread(coro, assignment_id: int):
    """在线程中运行协程，捕获异常避免静默丢失。"""
    new_loop = asyncio.new_event_loop()
    try:
        new_loop.run_until_complete(coro)
    except Exception as e:
        logger.error(
            "[dev] 作业 %d 的分析任务异常终止: %s",
            assignment_id, e, exc_info=True,
        )
    finally:
        new_loop.close()


def run_async_in_background(coro, assignment_id: int = 0):
    """Run an async coroutine in the background (fire-and-forget)."""
    try:
        # Try to get running loop
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
    except RuntimeError:
        # No running loop, create new one in thread
        import threading
        threading.Thread(
            target=_run_in_thread, args=(coro, assignment_id), daemon=True
        ).start()
        return
    # 保留引用直到任务完成，防止被 GC
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(lambda t: _handle_task_done(t, assignment_id))


def analyze_assignment_dev(assignment_id: int):
    """Dev-mode equivalent of analyze_assignment.delay()."""
    from app.tasks.analysis_tasks import _do_analyze
    logger.info("[dev] Starting analysis for assignment %d", assignment_id)
    run_async_in_background(_do_analyze(assignment_id), assignment_id)
