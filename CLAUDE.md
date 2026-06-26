# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# AI 助教系统

Claude Code 输出内容需用中文显示，不要在执行过程显示英文。

## 项目概述

AI 助教系统：上传作业试卷 → OCR 切割分题 → 多模态大模型评分/知识点提取 → 学情分析/错题重做/同类题生成 → AI Agent 对话式分析。

## 双模式设计

项目有两种运行模式，由 `backend/.env` 的 `DEV_MODE` 控制：

| 方面 | DEV_MODE=true（开发） | DEV_MODE=false（生产） |
|------|----------------------|----------------------|
| 数据库 | MySQL（仍需要） | MySQL |
| 文件存储 | 本地 `./uploads/` | MinIO (S3) |
| 异步任务 | 同步 inline 执行 | Celery + Redis |
| 向量检索 | 有损降级（返回空或 DB 查询） | Qdrant |
| Docker | 不需要 | Docker Compose |

## 数据库

- **必须使用 MySQL**，不允许 SQLite、PostgreSQL
- 连接信息在 `backend/.env` 的 `DATABASE_URL` 中配置
- 使用 `aiomysql` 驱动，连接格式：`mysql+aiomysql://user:pass@host:port/dbname`
- 数据库名：`ai_tutor`，字符集：utf8mb4
- ORM: SQLAlchemy 2.0 异步，模型定义在 `backend/app/models/`
- 迁移工具: Alembic，配置文件 `backend/alembic.ini`
- 启动时 `main.py` 的 `lifespan` 会自动 `create_all` + 执行内联 `_auto_migrate`（安全幂等）

## 运行方式

- Python 版本：**3.10**（PaddleOCR 兼容；3.14 有 PyTorch DLL 问题）
- 后端：`cd backend && C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe -m uvicorn app.main:app --reload --port 8000`
- 前端：`cd frontend && npm run dev`
- 一键启动：运行根目录 `start_dev.bat`
- 前端端口：5173（Vite 代理 `/api` → `localhost:8000`）
- API 文档：`http://localhost:8000/docs`

## 测试

- 测试工作不需要每次改代码后自动执行，由开发者完成，除非开发者主动要求 AI 测试

## 后端架构

```
backend/app/
├── main.py              # FastAPI 入口，lifespan 建表/迁移，注册路由，CORS/GZip 中间件
├── api/v1/              # 路由层（薄层，只做参数解析和响应格式化）
│   ├── auth.py          # 注册、登录、刷新令牌
│   ├── assignments.py   # 作业 CRUD、上传（接收 layout_type）
│   ├── questions.py     # 单题重分析、确认、生成同类题
│   ├── analytics.py     # 学情聚合接口
│   ├── error_questions.py  # 错题查询
│   ├── ai_tutor.py      # AI Agent SSE 对话入口 POST /api/v1/ai-tutor/chat
│   └── ai_questions.py  # AI 生成题库
├── core/
│   ├── config.py        # Pydantic Settings，读取 .env，单例 get_settings()
│   ├── security.py      # JWT 签发/验证、bcrypt 密码哈希、get_current_user 依赖
│   └── deps.py          # get_db (AsyncSession 依赖注入，自动 commit/rollback)
├── models/              # SQLAlchemy 2.0 Mapped 模型
│   ├── user.py          # users 表
│   ├── assignment.py    # assignments 表 + AssignmentStatus/LayoutType 枚举
│   ├── question.py      # assignment_questions 表 + analysis_tasks 表
│   └── ai_question.py   # AI 生成题库表
├── schemas/             # Pydantic 请求/响应模型
├── services/            # 业务逻辑层
│   ├── ai_grader.py            # 多模态 LLM 评分、答案识别
│   ├── similar_generator.py    # 同类题生成
│   ├── knowledge_extractor.py  # 知识点提取
│   ├── rag_service.py          # Qdrant 向量检索（dev 模式降级）
│   ├── analytics_aggregator.py # 学情聚合计算
│   ├── file_upload.py          # MinIO/本地文件操作
│   └── agent/                  # AI Agent (ReAct 模式)
│       ├── agent_executor.py   # ReAct 循环：LLM 思考 → 工具调用 → 结果返回 → 最终回答
│       ├── tools.py            # 4 个工具：search_analysis_chunks / get_assignment_score / get_error_knowledge / get_score_trend
│       └── prompts.py          # 系统提示词和用户上下文模板
├── tasks/               # 异步任务
│   ├── celery_app.py    # Celery 实例（生产模式）
│   ├── analysis_tasks.py   # 作业整体分析、单题重分析
│   ├── vector_tasks.py     # 向量化入库
│   └── dev_runner.py       # DEV 模式同步任务执行器（替代 Celery）
└── db/
    ├── session.py       # create_async_engine + async_session_factory
    └── base.py          # 导入所有模型（供 Alembic 和 create_all 使用）
```

### 关键流程

**作业上传 → 手动切割 → 分析：**
1. `POST /api/v1/assignments` 接收文件 + `layout_type`，创建作业记录（状态 `pending`）
2. 前端通过 `GET /{id}/source-pages` 获取渲染后的页面图片，用户在画布上框选题目区域
3. `POST /{id}/manual-split` 提交所有区域坐标，后端切图存入 MinIO/本地，状态变为 `splitted`
4. `POST /{id}/analyze` 触发异步分析（dev: `dev_runner.analyze_assignment_dev`，生产: Celery delay）
5. `analysis_tasks._do_analyze` 下载已有题目图片，调用多模态 LLM 逐题评分、识别答案、提取知识点
6. 分析结果写回 `assignment_questions` 各字段，更新 assignment 状态为 `completed`

**AI Agent 对话（SSE）：**
1. `POST /api/v1/ai-tutor/chat` → SSE StreamingResponse
2. `AgentExecutor.run()` 进入 ReAct 循环（最多 5 轮）
3. 每轮：LLM 决定调用工具或输出最终回答
4. 工具调用通过 `AgentTools.execute()` 执行，结果回传 LLM
5. SSE 事件类型：`reasoning` / `tool_call` / `tool_result` / `token` / `error` / `done`

**静态文件服务（仅 DEV 模式）：**
- `GET /api/v1/files/{file_path}` → `main.py` 中的 `serve_local_file` 直接从 `LOCAL_STORAGE_DIR` 读取
- 含路径穿越防护（resolve + prefix 校验）

## 前端架构

```
frontend/src/
├── pages/
│   ├── Login/                        # 登录页
│   ├── AssignmentManagement/
│   │   ├── UploadAssignment/         # 上传作业（含排版选择）
│   │   ├── AssignmentRecords/        # 作业记录列表（筛选 + 分页卡片）
│   │   ├── AssignmentDetail/         # 作业详情（总分 + AI 总评 + 题目卡片列表）
│   │   ├── ErrorRedo/                # 错题重做（筛选 + 错题卡片 + 同类题生成）
│   │   └── AIChallenge/              # AI 挑战
│   └── LearningAnalytics/            # 学情分析图表（@ant-design/charts）
├── components/                       # 可复用组件
├── services/                         # API 封装（axios 实例 + JWT 拦截 + 自动刷新）
├── hooks/                            # useAuth / useSSE / useUpload / useReanalysis
└── utils/
```

- **技术栈**: React 18 + TypeScript + Vite + Ant Design 5 + TanStack Query + @ant-design/charts
- **路由**: react-router-dom v6，`App.tsx` 定义全部路由
- **鉴权**: JWT access/refresh token，`api.ts` 拦截器自动附加 token + 401 自动刷新 + 队列化并发请求
- **布局**: AppLayout（顶部导航） → AssignmentLayout（侧边栏） → 各子页面
- **全局 AI 助手**: AIFloatButton（悬浮按钮）+ ChatDrawer（聊天抽屉，SSE 流式对话）
- **代理**: Vite 开发服务器 `/api` → `http://localhost:8000`

## 数据库表

核心表（定义见 `backend/app/models/`）：

| 表 | 用途 |
|---|------|
| `users` | 用户，含 hashed_password, role |
| `assignments` | 作业，含 layout_type, status, file_url, ai_summary |
| `assignment_questions` | 单题，含 student_answer, correct_answer, score, knowledge_points(JSON), common_mistakes(JSON), bbox 坐标 |
| `analysis_tasks` | 异步分析任务追踪 |
| `ai_generated_questions` | AI 生成的题目 |
| `ai_question_answers` | AI 题目的作答记录 |

## 作业状态流转

```
pending → splitting → splitted → grading → completed
                                        ↓
                                     failed
```

（旧版 `processing` 状态兼容保留）
