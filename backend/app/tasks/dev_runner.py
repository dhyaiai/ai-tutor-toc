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


async def _safe_analyze(assignment_id: int):
    """Wrap _do_analyze with error handling to mark assignment as FAILED on crash."""
    from app.tasks.analysis_tasks import _do_analyze, _mark_failed
    try:
        await _do_analyze(assignment_id)
    except Exception as exc:
        logger.error("[dev] Analysis failed for assignment %d: %s", assignment_id, exc, exc_info=True)
        try:
            await _mark_failed(assignment_id, str(exc))
        except Exception as mark_exc:
            logger.error("[dev] Failed to mark assignment %d as FAILED: %s", assignment_id, mark_exc)


def analyze_assignment_dev(assignment_id: int):
    """Dev-mode equivalent of analyze_assignment.delay()."""
    logger.info("[dev] Starting analysis for assignment %d", assignment_id)
    run_async_in_background(_safe_analyze(assignment_id))
