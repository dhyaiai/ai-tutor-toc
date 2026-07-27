---
kind: error_handling
name: 基于 FastAPI HTTPException 的分散式错误处理
category: error_handling
scope:
    - '**'
source_files:
    - backend/app/main.py
    - backend/app/api/v1/auth.py
    - backend/app/api/v1/assignments.py
    - backend/app/api/v1/ai_questions.py
    - backend/app/services/composition_service.py
    - backend/app/services/file_upload.py
---

## 1. 系统/方法概述
- 后端基于 FastAPI，**未定义统一的自定义异常类或全局异常处理器（`@app.exception_handler`）**。所有业务错误通过在各路由函数中直接 `raise HTTPException(status_code, detail=...)` 抛出。
- 启动期关键配置缺失时（如生产环境未设置 `SECRET_KEY`），在 `lifespan` 中直接 `raise RuntimeError(...)` 阻止进程启动。
- 日志使用标准 `logging` 模块（各文件 `import logging` 后调用 `logger.error(...)`）记录异常堆栈与上下文信息；未发现结构化日志框架（structlog、loguru 等）。
- 前端侧未实现统一错误拦截器（axios interceptor），错误处理主要依赖后端返回的 HTTP 状态码与 `detail` 文本。

## 2. 关键文件与位置
- 应用入口与中间件：`backend/app/main.py`
  - 注册 CORS / GZip 中间件，无全局异常处理器。
  - `lifespan` 中校验 `SECRET_KEY`，失败则 `raise RuntimeError`。
- API 路由层（大量 `HTTPException` 示例）：
  - `backend/app/api/v1/auth.py` — 409 重复用户、401 认证失败等。
  - `backend/app/api/v1/assignments.py` — 400/413/500 文件上传、MinIO 超时等。
  - `backend/app/api/v1/ai_questions.py` — 404 题目不存在、500 生成失败等。
- 服务层（仅 `logger.error` 记录，不抛出自定义异常）：
  - `backend/app/services/composition_service.py`、`oral_service.py`、`similar_generator.py`、`file_upload.py`、`agent/tools.py` 等。
- 迁移脚本中的数据库异常处理：`backend/app/main.py` 的 `_auto_migrate` 针对 MySQL 1060/1061 及 SQLite “duplicate column” 做静默跳过。

## 3. 架构与约定
- **错误传播路径**：Service → Router → `HTTPException` → FastAPI 默认 JSON 响应 `{"detail": "..."}`。
- **状态码约定**（从现有代码归纳）：
  - 400：参数/格式错误（如“未提供文件”、“无效的文件类型”）。
  - 401：认证失败（账号密码错误、token 无效/过期）。
  - 403：权限不足（dev 模式下访问外部目录被拒绝）。
  - 404：资源不存在（作业/题目/文件找不到）。
  - 409：冲突（用户名已存在）。
  - 413：请求体过大（文件超过限制）。
  - 500：服务端内部错误（MinIO 超时/失败、AI 生成失败）。
- **日志策略**：在捕获到具体异常后先 `logger.error(..., exc_info=True)` 记录完整堆栈，再向上抛出 `HTTPException`，使客户端收到友好消息而运维可回溯细节。
- **幂等迁移容错**：对“列/索引已存在”等可忽略错误做条件判断并 `continue`，避免迁移中断。

## 4. 开发者应遵循的规则
1. **不要吞掉异常**：在 `except Exception as e:` 中至少记录 `logger.error(..., exc_info=True)`，然后 `raise HTTPException` 明确状态码，禁止静默返回空结果。
2. **优先使用语义化状态码**：按上述约定选择 4xx/5xx，不要在 `detail` 里塞入技术堆栈。
3. **对外消息面向用户**：`detail` 字段应为可读中文提示；内部调试信息放入日志。
4. **长耗时 I/O 必须加超时**：参考 `asyncio.wait_for(..., timeout=STORAGE_TIMEOUT)` 模式，捕获 `TimeoutError` 后返回 500 并记录日志。
5. **启动期强约束**：生产环境缺少关键配置时应 `raise RuntimeError` 阻止启动，而非降级运行。
6. **暂不建议自行定义异常基类**：当前项目规模下引入统一异常体系收益有限；若未来需要，可在 `app/core/exceptions.py` 集中定义并在 `main.py` 注册 `@app.exception_handler` 以替代分散的 `HTTPException`。

## 5. 已知缺口（改进建议）
- 缺少全局异常处理器，无法统一包装响应结构（例如增加 `code`、`trace_id` 字段）。
- 未定义领域异常类，难以区分“业务校验失败”和“系统故障”，不利于前端差异化 UI 展示。
- 日志未接入集中式收集（ELK/Sentry），排查线上问题依赖本地文件。
