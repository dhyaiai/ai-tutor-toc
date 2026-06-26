"""
Development task runner that executes tasks synchronously without Celery/Redis.
Used when DEV_MODE=true.
"""
import asyncio
import logging

logger = logging.getLogger(__name__)


def run_async_in_background(coro):
    """Run an async coroutine in the background (fire-and-forget)."""
    try:
        # Try to get running loop
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        # No running loop, create new one in thread
        import threading
        def _run():
            new_loop = asyncio.new_event_loop()
            new_loop.run_until_complete(coro)
        threading.Thread(target=_run, daemon=True).start()


def analyze_assignment_dev(assignment_id: int):
    """Dev-mode equivalent of analyze_assignment.delay()."""
    from app.tasks.analysis_tasks import _do_analyze
    logger.info("[dev] Starting analysis for assignment %d", assignment_id)
    run_async_in_background(_do_analyze(assignment_id))
