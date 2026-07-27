---
kind: external_dependency
name: Redis 缓存与任务代理
slug: redis
category: external_dependency
category_hints:
    - vendor_identity
    - framework_behavior
scope:
    - '**'
---

### Redis 缓存与任务代理
- **角色**：会话缓存（短期记忆）、Celery 任务队列 Broker、实时语音流中转
- **集成点**：`REDIS_URL=redis://localhost:6379/0`，Celery 通过 redis 作为 broker 和 backend
- **使用模式**：开发模式可选用 SQLite+本地存储，生产环境需独立 Redis 实例；会话活跃期数据缓存于 Redis，持久化落库 MySQL
- **注意**：DEV_MODE=true 时可跳过 Redis，但 Celery 任务无法执行