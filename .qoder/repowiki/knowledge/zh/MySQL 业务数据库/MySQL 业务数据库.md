---
kind: external_dependency
name: MySQL 业务数据库
slug: mysql
category: external_dependency
category_hints:
    - vendor_identity
    - client_constraint
scope:
    - '**'
---

### MySQL 业务数据库
- **角色**：用户、作业、题目、学情状态、对话历史等业务数据的持久化存储
- **集成点**：`DATABASE_URL` 配置，默认开发模式使用 `sqlite+aiosqlite:///./ai_tutor.db`，生产模式切换为 `mysql+asyncmy://user:pass@host:port/dbname`
- **使用模式**：SQLAlchemy 2.0 异步 ORM + Alembic 迁移；应用启动时自动创建表结构并执行增量迁移
- **约束**：生产环境要求 MySQL 8.0，需提前创建数据库；SQLite 仅用于本地开发调试