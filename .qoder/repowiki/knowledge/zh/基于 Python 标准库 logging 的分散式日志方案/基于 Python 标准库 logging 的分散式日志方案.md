---
kind: logging_system
name: 基于 Python 标准库 logging 的分散式日志方案
category: logging_system
scope:
    - '**'
source_files:
    - backend/app/main.py
    - backend/alembic/env.py
    - backend/app/api/v1/assignments.py
    - backend/app/api/v1/ai_questions.py
    - backend/app/api/v1/questions.py
    - backend/app/services/ai_grader.py
    - backend/app/services/composition_service.py
    - backend/app/services/oral_service.py
    - backend/app/services/knowledge_tracker.py
    - backend/app/services/agent/agent_executor.py
    - backend/app/services/agent/tools.py
    - backend/requirements.txt
---

本仓库未引入第三方日志框架（如 loguru、structlog），也未在应用启动时统一配置 uvicorn 或 FastAPI 的日志系统。整体采用 Python 标准库 `logging` 模块，在各模块内自行获取 logger 实例，属于“分散式”日志模式。

## 使用方式与约定
- 每个需要日志的模块通过 `logger = logging.getLogger(__name__)` 获取以模块名命名的 logger 实例。
- 日志级别主要使用 `info`、`warning`、`error`；未发现 `debug` 级别的系统性使用。
- 日志消息普遍采用 `%s` 占位符拼接参数，而非 f-string，便于延迟格式化。
- 错误日志常附带 `exc_info=True` 输出完整堆栈。
- 启动阶段和数据库迁移阶段使用 `print(..., flush=True)` 直接输出到 stdout，未走 logging 体系。

## 关键文件分布
- 应用入口 `backend/app/main.py`：仅用 `print` 输出启动/迁移信息，未对 uvicorn 或 FastAPI 做全局日志配置。
- Alembic 迁移 `backend/alembic/env.py`：通过 `logging.config.fileConfig(config_file_name)` 加载 alembic.ini 中的 logging 配置，但仓库中未见对应的 `logging.conf` 文件，因此默认使用 Python logging 的根级空配置。
- API 层与服务层广泛使用：
  - `backend/app/api/v1/assignments.py`、`ai_questions.py`、`questions.py`、`error_questions.py`
  - `backend/app/services/ai_grader.py`、`composition_service.py`、`oral_service.py`、`knowledge_tracker.py`、`agent/agent_executor.py`、`agent/tools.py` 等
- 依赖清单 `backend/requirements.txt` 中不包含任何第三方日志库。

## 架构与现状评估
- **无集中配置**：没有在 `main.py` 或独立的 `logging_config.py` 中统一设置 root logger、Handler、Formatter 或 Log Level，导致各进程运行时的日志行为取决于 Python 默认行为（通常只输出 WARNING 及以上到 stderr）。
- **无结构化字段**：日志为纯文本字符串，没有统一的 JSON 结构或固定字段（如 request_id、user_id、trace_id），不利于集中采集与检索。
- **无请求链路追踪**：FastAPI 未挂载 `RequestLoggingMiddleware` 之类的中间件，HTTP 请求的入参、耗时、状态码未被自动记录。
- **Alembic 独立配置**：迁移脚本尝试从 alembic.ini 读取 logging 配置，但缺少对应配置文件，实际运行时不会生效。

## 开发者应遵循的规则
1. 新增模块如需日志，统一使用 `import logging; logger = logging.getLogger(__name__)` 模式。
2. 优先使用 `logger.info/warning/error`，避免混用 `print`（仅保留启动/迁移阶段的临时输出）。
3. 错误日志尽量附带 `exc_info=True`，并包含关键上下文参数（如用户 ID、作业 ID）以便定位问题。
4. 暂不强制要求 JSON 结构化输出，但建议在关键业务路径上保持字段一致，为后续接入集中式日志平台预留空间。
5. 若未来需要统一日志格式、级别或输出目标，应在 `app/main.py` 启动阶段集中配置 root logger，并在 `alembic/env.py` 中复用同一份配置。