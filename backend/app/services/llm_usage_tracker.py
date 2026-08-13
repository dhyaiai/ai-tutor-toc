"""
LLM Token 用量自动追踪器

通过全局包装 openai AsyncCompletions.create，对系统内所有大模型调用
（AI 批改 / Agent 对话 / 同类题生成 / 作文批改 / 口语测评等）统一记录
Token 消耗，写入 llm_usage_logs 表，供数据看板统计使用。

设计要点：
- 一处安装、全局生效，无需改动各业务调用点
- 非流式调用：直接读取 response.usage
- 流式调用：包装异步迭代器，捕获上游在末尾 chunk 中携带的 usage
  （部分 OpenAI 兼容服务默认返回）；未返回时 Token 记 0，但仍计入调用量
- 记录失败绝不影响业务调用（fail-silent，仅记日志）
"""

import logging

logger = logging.getLogger(__name__)

_installed = False


# 用量写入缓冲队列：攒批写入避免每次调用新建 session（减少连接池压力）
import asyncio as _asyncio
_pending_usage: list[dict] = []
_flush_task: _asyncio.Task | None = None
_FLUSH_INTERVAL = 5.0  # 秒，定时批量写入
_BATCH_SIZE = 10  # 达到此数量立即写入


async def flush_on_shutdown() -> None:
    """供 FastAPI lifespan 在服务关闭时调用，确保缓冲区数据落库。

    用法：在 main.py 的 lifespan yield 之后 await flush_on_shutdown()。
    """
    global _flush_task
    if _flush_task and not _flush_task.done():
        _flush_task.cancel()
        try:
            await _flush_task
        except _asyncio.CancelledError:
            pass
    if _pending_usage:
        await _flush_usage()


def flush_pending_sync() -> None:
    """同步批量写入（供 Celery 信号等无事件循环上下文时兜底落库）。

    Celery 任务各自通过 asyncio.run 运行独立事件循环，其内调度的延迟
    flush 任务会随循环关闭被取消；本函数在任务结束/进程退出时同步写入，
    避免用量记录长期滞留缓冲区甚至丢失。
    """
    global _flush_task
    # 丢弃可能属于其它（已关闭）事件循环的过期 flush 任务
    _flush_task = None
    try:
        _asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        # 已有运行中的循环（如 FastAPI 请求上下文），延迟写入由循环内任务负责
        return
    try:
        import asyncio
        asyncio.run(_flush_usage())
    except Exception:
        logger.warning("[llm_usage] flush_pending_sync failed", exc_info=True)


async def cleanup_old_usage_logs(retention_days: int = 90) -> int:
    """删除 N 天前的 LLM 用量日志（保留策略，防止表无限膨胀）。

    每次 LLM 调用写入一行（含失败调用），长期运行磁盘/DB 会持续增长；
    看板只需近期数据，按保留期定期清理即可。仅在服务启动时执行一次，
    删除量过大时批量删除避免长事务。
    """
    try:
        from datetime import datetime, timedelta

        from sqlalchemy import delete

        from app.db.session import async_session_factory
        from app.models.llm_usage import LlmUsageLog

        cutoff = datetime.now() - timedelta(days=retention_days)
        deleted = 0
        async with async_session_factory() as db:
            while True:
                result = await db.execute(
                    delete(LlmUsageLog)
                    .where(LlmUsageLog.created_at < cutoff)
                    .limit(5000)
                )
                await db.commit()
                rows = result.rowcount
                if not rows:
                    break
                deleted += rows
        if deleted:
            logger.info("[llm_usage] 已清理 %d 条超过 %d 天的旧用量日志", deleted, retention_days)
        return deleted
    except Exception as e:
        logger.warning("[llm_usage] 清理旧用量日志失败: %s", e)
        return 0


async def _flush_usage() -> None:
    """将缓冲区的用量记录批量写入数据库"""
    global _pending_usage, _flush_task
    if not _pending_usage:
        return
    # 取出当前缓冲区（避免与新的 append 冲突）
    batch = _pending_usage.copy()
    _pending_usage.clear()
    try:
        from app.db.session import async_session_factory
        from app.models.llm_usage import LlmUsageLog

        async with async_session_factory() as db:
            for item in batch:
                db.add(LlmUsageLog(
                    model=item["model"],
                    prompt_tokens=item["prompt"],
                    completion_tokens=item["completion"],
                    total_tokens=item["total"],
                ))
            await db.commit()
    except Exception as e:
        logger.warning("[llm_usage] Failed to flush usage batch: %s", e)
    finally:
        _flush_task = None


async def _schedule_flush() -> None:
    """调度批量写入（延迟执行，让后续写入有机会合并到同一批次）"""
    global _flush_task
    # 注意：任务可能已因所属事件循环关闭而被取消（Celery 任务各自独立 loop），
    # 此时引用仍残留，必须用 done() 判断才能重新调度，否则后续写入永不落库
    if _flush_task is not None and not _flush_task.done():
        return
    _flush_task = _asyncio.create_task(_delayed_flush())


async def _delayed_flush() -> None:
    """延迟执行批量写入"""
    await _asyncio.sleep(_FLUSH_INTERVAL)
    await _flush_usage()


async def _record_usage(model: str, usage) -> None:
    """将一次调用的用量写入缓冲区（批量写入，减少连接池压力）"""
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
    completion = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
    total = int(getattr(usage, "total_tokens", 0) or 0) if usage else 0
    if total == 0:
        total = prompt + completion
    _pending_usage.append({
        "model": (model or "")[:64],
        "prompt": prompt,
        "completion": completion,
        "total": total,
    })
    # 达到批量阈值立即写入，否则延迟写入
    if len(_pending_usage) >= _BATCH_SIZE:
        if _flush_task is not None:
            # 可能持有来自其它（已关闭）事件循环的过期任务，直接丢弃引用，
            # 不要 await（跨循环 await 会抛 RuntimeError）
            if not _flush_task.done():
                _flush_task.cancel()
                try:
                    await _flush_task
                except _asyncio.CancelledError:
                    pass
            _flush_task = None
        await _flush_usage()
    else:
        await _schedule_flush()


class _TrackedAsyncStream:
    """流式响应包装器：透传所有 chunk，结束时记录 usage（保证只记一次）"""

    def __init__(self, stream, model: str):
        self._stream = stream
        self._model = model
        self._usage = None
        self._recorded = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self._stream.__anext__()
        except StopAsyncIteration:
            await self._finalize()
            raise
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            self._usage = usage
        return chunk

    async def __aenter__(self):
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._finalize()
        return await self._stream.__aexit__(exc_type, exc, tb)

    async def close(self):
        await self._finalize()
        await self._stream.close()

    def __getattr__(self, name):
        # 其余属性/方法透传给原始 stream 对象
        if self._stream is None:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return getattr(self._stream, name)

    async def _finalize(self):
        if self._recorded:
            return
        self._recorded = True
        await _record_usage(self._model, self._usage)


def install_llm_usage_tracker() -> None:
    """安装全局 Token 用量追踪（幂等，可在 FastAPI 与 Celery 进程各调用一次）"""
    global _installed
    if _installed:
        return
    try:
        from openai.resources.chat.completions import AsyncCompletions
    except ImportError:
        logger.warning("[llm_usage] openai SDK not available, tracker not installed")
        return

    original_create = AsyncCompletions.create

    async def tracked_create(self, *args, **kwargs):
        model = str(kwargs.get("model", ""))
        # 流式调用：自动注入 stream_options 让服务端在末尾 chunk 返回 usage
        # （OpenAI 兼容接口默认不返回流式 usage，需显式请求）
        if kwargs.get("stream") and "stream_options" not in kwargs:
            kwargs["stream_options"] = {"include_usage": True}
        try:
            result = await original_create(self, *args, **kwargs)
        except Exception:
            # 异常路径也记录用量（API 超时时 prompt token 可能已消耗）
            await _record_usage(model, None)
            raise
        if kwargs.get("stream"):
            return _TrackedAsyncStream(result, model)
        await _record_usage(model, getattr(result, "usage", None))
        return result

    AsyncCompletions.create = tracked_create
    _installed = True
    logger.info("[llm_usage] Token usage tracker installed")
