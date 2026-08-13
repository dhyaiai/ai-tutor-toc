"""
生产模式后台任务状态存储：Redis（跨 worker 共享）。

背景：DEV 模式各路由用进程内 TTLCache（单进程天然可见）；生产模式多 worker
部署时，进程内缓存互相不可见——后台任务可能跑在 worker A，前端轮询打到
worker B，必须把任务状态/结果存到 Redis 共享存储，否则轮询返回 not_found、
任务状态永远停在 pending（详见 questions/ai_questions/upload_questions 的
DEV_MODE 分派分支）。

本模块只服务"任务状态缓存"这一场景（key 短、TTL 短、JSON 值）：
- 懒连接 + 复用单个连接（模块级单例，同步客户端）
- 读写失败仅记日志返回 None：状态存储是"轮询可见性"优化，任务执行本身
  不依赖它，Redis 故障时最坏结果是前端暂时轮询不到结果（不会崩业务）

为什么用同步 redis 客户端而不是 redis.asyncio：
Celery worker 中 _run_async 每次任务都 asyncio.run() 新建并关闭 event loop，
而 redis.asyncio 连接的 transport/StreamReader 绑定在首次创建它的 loop 上，
第 2 个任务起在新 loop 里 await 旧 loop 的 future 会抛
"Future attached to a different loop"（见 analysis_tasks._run_async），
导致除首个任务外所有状态读写静默失败。同步客户端与 event loop 无关，
配合 asyncio.to_thread 可在 API 进程（uvicorn 单 loop 常驻）与
Celery worker（每任务新 loop）两种形态下复用同一连接。
"""

import asyncio
import json
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_client = None  # 模块级惰性单例（redis 同步客户端，跨线程/跨 event loop 安全）
_client_lock = threading.Lock()  # 首次初始化加锁，避免并发首次调用创建双连接


def _get_client():
    """惰性创建 Redis 连接（生产模式才被调用）。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import redis
                from app.core.config import get_settings
                _client = redis.Redis.from_url(
                    get_settings().REDIS_URL,
                    decode_responses=True,
                    max_connections=8,
                )
    return _client


def _key(name: str) -> str:
    # 统一前缀，避免与 Celery broker 的 key 冲突
    return f"task_state:{name}"


async def redis_state_set(name: str, value: dict, ttl: int = 1800) -> None:
    """写任务状态（生产模式由后台任务写、轮询接口读）。

    ttl 应与对应任务的最坏时长匹配（长转录任务显式传大 TTL），
    否则 key 先于任务结束过期，前端轮询会 not_found 误判失败。
    """
    try:
        # 同步客户端 + to_thread：不阻塞事件循环，且与 event loop 生命周期无关
        await asyncio.to_thread(
            _get_client().set,
            _key(name),
            json.dumps(value, ensure_ascii=False),
            ex=ttl,
        )
    except Exception:
        logger.exception("Redis 任务状态写入失败 key=%s", name)


async def redis_state_get(name: str) -> Optional[dict]:
    """读任务状态；不存在或读取失败返回 None。"""
    try:
        raw = await asyncio.to_thread(_get_client().get, _key(name))
        return json.loads(raw) if raw else None
    except Exception:
        logger.exception("Redis 任务状态读取失败 key=%s", name)
        return None


async def redis_state_setnx(name: str, value: dict, ttl: int = 1800) -> bool:
    """SET NX 占位：任务启动前原子占位，防止并发请求重复触发同一任务。

    返回 False 表示该任务已存在（占用中），调用方应直接返回"已有任务进行中"。
    Redis 连接故障时同样返回 False（吞异常仅记日志）——调用方语义保持一致，
    最坏情况是误报"进行中"而非任务重复执行。
    """
    try:
        ok = await asyncio.to_thread(
            _get_client().set,
            _key(name),
            json.dumps(value, ensure_ascii=False),
            ex=ttl,
            nx=True,
        )
        return bool(ok)
    except Exception:
        logger.exception("Redis 任务占位失败 key=%s", name)
        return False


async def redis_state_del(name: str) -> None:
    """删除任务占位锁（任务启动时释放，允许后续再次触发同类任务）。

    仅生产模式调用；失败仅记日志（TTL 到期兜底自动释放，不会卡死后续触发）。
    """
    try:
        await asyncio.to_thread(_get_client().delete, _key(name))
    except Exception:
        logger.exception("Redis 任务占位锁释放失败 key=%s", name)
