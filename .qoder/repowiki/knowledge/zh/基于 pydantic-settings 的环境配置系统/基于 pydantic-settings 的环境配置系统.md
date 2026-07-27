---
kind: configuration_system
name: 基于 pydantic-settings 的环境配置系统
category: configuration_system
scope:
    - '**'
source_files:
    - backend/app/core/config.py
    - backend/.env
    - .env.example
    - frontend/.env.example
    - backend/app/main.py
---

## 系统概述
后端采用 `pydantic_settings.BaseSettings` 作为统一配置加载器，通过 `.env` 文件与环境变量注入，结合启动期校验与默认值回退，实现开发/生产环境可切换的配置体系。

## 核心文件与职责
- `backend/app/core/config.py`：定义 `Settings` Pydantic 模型，集中声明所有运行时配置项（数据库、Redis、MinIO、JWT、LLM、Qdrant、上传大小等），并通过 `model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}` 指定默认 .env 路径；`get_settings()` 使用 `@lru_cache()` 缓存单例。
- `backend/.env`：本地开发实际配置（MySQL、Redis、MinIO、阿里云 LLM、Qdrant 等）。
- `.env.example`：仓库级模板，包含开发模式（SQLite + 本地存储）与生产模式（Docker 相关）两组注释示例。
- `frontend/.env.example`：前端 Vite 环境变量模板，仅暴露 `VITE_API_BASE_URL`。
- `backend/app/main.py`：在 FastAPI lifespan 中调用 `_ensure_secret_key()` 做启动期安全校验，DEV_MODE 下自动生成 SECRET_KEY，生产模式下缺失则直接抛错拒绝启动。

## 架构与设计约定
1. **单一来源**：所有后端配置集中在 `app.core.config.Settings`，模块通过 `from app.core.config import get_settings` 获取，禁止散落 `os.environ` 直读。
2. **分层覆盖**：`.env` → 操作系统环境变量 → Pydantic 字段默认值，优先级依次递减；新增配置只需在 Settings 中添加字段并设置合理默认值。
3. **环境开关**：`DEV_MODE=true` 时启用 SQLite + 本地 uploads 目录 + 同步任务，无需 Docker；设为 false 则走 MySQL/Redis/MinIO/Qdrant 的完整栈。
4. **敏感信息保护**：`SECRET_KEY`、`MINIO_SECRET_KEY`、`LLM_API_KEY` 等关键字段无默认值或仅有占位符，启动期强制校验，防止误用默认 key 上线。
5. **前端隔离**：前端不共享后端 .env，仅通过 `VITE_API_BASE_URL` 指向后端 API 前缀，避免将密钥泄露到浏览器。

## 开发者规范
- 新增配置项：在 `Settings` 类中声明字段并提供默认值，同时在 `.env.example` 添加对应注释行说明用途。
- 不要硬编码 URL、密钥、端口等可变参数；一律通过环境变量或 .env 注入。
- 生产部署时务必提供非默认的 `SECRET_KEY`，且 `DEV_MODE=false`，由容器编排平台注入真实凭据。
- 前端新增 `VITE_` 前缀的环境变量需同步更新 `frontend/.env.example`。