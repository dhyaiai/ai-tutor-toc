# CLAUDE.md

# AI 助教系统

本系统为C端产品,旨在为用户提供个性化的学习空间

# 项目要求
代码要有详细的中文注释,方便后续的维护

## 项目概述

AI 助教系统：上传作业试卷 → 切割分题 → 多模态大模型评分/知识点提取 → 学情分析/错题重做/同类题生成 → AI Agent 对话式分析。

主要功能板块：
- 作业管理：上传 → 手动切割 → 分析 → 详情；错题重做、AI 挑战（AI 生成题）、我的收藏（收藏 + 上传自有试题）
- 学情分析：成绩/错题/趋势图表
- 作文批改：语文/英语，按中高考标准评分 + 逐处修改建议 + 范文 + PDF 报告
- 口语测评：英语听力、单词听写、普通话测评（讯飞 ISE）
- AI Agent：关键词路由 + ReAct 工具调用对话（成绩/错题/趋势/报告/订正本/学习计划/作文/讲解/知识状态）
- 数据看板：LLM Token 用量统计

## 双模式设计

项目有两种运行模式，由 `backend/.env` 的 `DEV_MODE` 控制：

| 方面 | DEV_MODE=true（开发） | DEV_MODE=false（生产） |
|------|----------------------|----------------------|
| 数据库 | MySQL（仍需要） | MySQL |
| 文件存储 | 本地 `./uploads/` | MinIO (S3) |
| 异步任务 | 同步 inline 执行（dev_runner 后台任务） | Celery + Redis |
| 任务状态 | 进程内 TTLCache | Redis（`services/redis_state.py`，多 worker 共享） |
| Docker | 不需要 | 不需要（Windows 脚本部署） |

> 注：RAG/向量检索已整体移除（rag_service.py、vector_tasks.py、Milvus/Qdrant 已删除），Agent 工具不再有向量检索。

## 数据库

- **必须使用 MySQL**，不允许 SQLite、PostgreSQL
- 连接信息在 `backend/.env` 的 `DATABASE_URL` 中配置
- 使用 `aiomysql` 驱动，连接格式：`mysql+aiomysql://user:pass@host:port/dbname`
- 数据库名：`ai_tutor`，字符集：utf8mb4
- ORM: SQLAlchemy 2.0 异步，模型定义在 `backend/app/models/`
- 迁移工具: Alembic，配置文件 `backend/alembic.ini`
- 启动时 `main.py` 的 `lifespan` 会自动 `create_all` + 执行内联 `_auto_migrate`（安全幂等，白名单错误码 1050/1054/1060/1061/1091/1092/1146/1826/1138 忽略；生产模式非白名单错误直接启动失败）

## 运行方式

- Python 版本：**3.10**（PaddleOCR 兼容；3.14 有 PyTorch DLL 问题）
- 后端：`cd backend && C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe -m uvicorn app.main:app --reload --port 8000`
- 前端：`cd frontend && npm run dev`
- 一键启动：运行根目录 `start_dev.bat`（开发）；`start_public.bat`（公网，自启 MySQL + Cloudflare Tunnel）
- 前端端口：5173（Vite 代理 `/api` → `localhost:8000`）
- API 文档：`http://localhost:8000/docs`

## 测试

- 测试工作不需要每次改代码后自动执行，由开发者完成，除非开发者主动要求 AI 测试

## 用户体系

- 登录账号为**手机号**（users.phone 唯一），username 仅为显示名（可空）
- **注册接口已移除**：开户走管理员 `POST /users`；空库启动时自动引导创建初始管理员（`FIRST_ADMIN_PHONE`/`FIRST_ADMIN_PASSWORD`，dev 模式随机密码打印控制台）
- 角色只有 USER / ADMIN（教师角色已统一降级为 USER）
- **单设备登录**：`SINGLE_DEVICE_LOGIN=true` 时新登录踢掉旧设备（users.token_version 校验）

## 后端架构

```
backend/app/
├── main.py              # FastAPI 入口，lifespan 建表/迁移/引导管理员/卡死自愈，注册路由，CORS/GZip/安全头
├── api/v1/              # 路由层（薄层，只做参数解析和响应格式化）
│   ├── auth.py          # 登录、刷新令牌（无注册）
│   ├── users.py         # 用户管理（管理员开户）
│   ├── assignments.py   # 作业 CRUD、上传（接收 layout_type）、手动切割、分析触发
│   ├── questions.py     # 单题重分析、确认、生成同类题、分步讲解
│   ├── upload_questions.py  # 上传自有试题（图片/PDF → 转录入库）
│   ├── ai_questions.py  # AI 生成题库（挑战/作答）
│   ├── favorites.py     # 我的收藏（错题/AI题）
│   ├── error_questions.py  # 错题查询
│   ├── analytics.py     # 学情聚合接口
│   ├── ai_tutor.py      # AI Agent SSE 对话入口 POST /api/v1/ai-tutor/chat
│   ├── conversations.py # 会话管理（多会话、消息落库）
│   ├── personality.py   # 助教性格配置
│   ├── compositions.py  # 作文批改（异步状态机）
│   ├── oral_assessments.py  # 口语测评（听力/听写/普通话/记录）
│   └── usage_stats.py   # LLM 用量统计（数据看板）
├── core/
│   ├── config.py        # Pydantic Settings，读取 .env，单例 get_settings()
│   ├── security.py      # JWT 签发/验证、bcrypt 密码哈希、get_current_user 依赖
│   └── deps.py          # get_db (AsyncSession 依赖注入，自动 commit/rollback)
├── models/              # SQLAlchemy 2.0 Mapped 模型
│   ├── user.py          # users（phone 登录 + token_version 单设备）
│   ├── assignment.py    # assignments 表 + AssignmentStatus/LayoutType 枚举
│   ├── question.py      # assignment_questions 表（父题/子题层级、question_text、bbox）+ analysis_tasks
│   ├── ai_question.py   # AI 生成题库（含上传转录 source='upload'）+ 作答记录
│   ├── composition.py   # 作文批改记录
│   ├── oral_assessment.py  # 听力/听写/普通话/统一作业记录
│   ├── conversation.py  # 会话 + 消息
│   ├── personality.py   # 助教性格配置
│   ├── knowledge_state.py  # 知识点掌握状态
│   ├── favorite.py      # 收藏
│   └── llm_usage.py     # LLM Token 用量日志
├── schemas/             # Pydantic 请求/响应模型
├── services/            # 业务逻辑层
│   ├── ai_grader.py            # 多模态 LLM 评分、答案识别（Qwen 视觉）
│   ├── image_preprocess.py     # 图像预处理（OCR/裁剪/压缩）
│   ├── question_transcriber.py # 题目转录（图片/PDF → 文本+LaTeX）
│   ├── pdf_renderer.py         # PDF 渲染（试卷转图）
│   ├── similar_generator.py    # 同类题生成（大小题/组卷）
│   ├── knowledge_extractor.py  # 知识点提取
│   ├── knowledge_tracker.py    # 知识点状态读写（user_knowledge_state）
│   ├── analytics_aggregator.py # 学情聚合计算
│   ├── composition_service.py  # 作文批改（中高考评分标准、异步）
│   ├── oral_service.py         # 口语测评
│   ├── xfyun_ise.py            # 讯飞流式语音评测（普通话朗读）
│   ├── explain_service.py      # 分步讲解
│   ├── personality_service.py  # 助教性格模板
│   ├── llm_usage_tracker.py    # LLM Token 用量全局追踪
│   ├── llm_json.py             # LLM JSON 请求统一封装（重试 + 容错解析）
│   ├── file_upload.py          # MinIO/本地文件操作
│   ├── file_server.py          # 本地静态文件服务（仅 DEV：路径穿越防护 + 私有目录鉴权）
│   ├── redis_state.py          # 生产模式任务状态 Redis 存储（跨 worker 共享）
│   ├── question_pipeline/      # LangGraph 智能出题流水线（search→calibrate→transform→verify 回流重试）
│   └── agent/                  # AI Agent (关键词路由 + ReAct)
│       ├── agent_executor.py   # ReAct 循环：LLM 思考 → 工具调用 → 结果返回 → 最终回答
│       ├── tools.py            # 11 个工具（@tool 装饰器自动注册，见下方清单）
│       ├── tool_router.py      # 关键词路由层：命中→只传工具子集；未命中→纯聊天秒回
│       ├── route_config.py     # 路由规则表（关键词 → 工具，可热更新）
│       └── prompts.py          # 系统提示词和用户上下文模板
├── tasks/               # 异步任务
│   ├── celery_app.py    # Celery 实例（生产模式）
│   ├── analysis_tasks.py # 作业整体分析、单题重分析、卡死自愈（reconcile_stuck_assignment）
│   ├── composition_tasks.py # 作文批改异步任务
│   └── dev_runner.py    # DEV 模式同步任务执行器（asyncio 后台任务，替代 Celery）
└── db/
    ├── session.py       # create_async_engine + async_session_factory
    └── base.py          # 导入所有模型（供 Alembic 和 create_all 使用）
```

### Agent 工具清单（tools.py，11 个）

- 查询类：`get_assignment_score`(成绩统计)、`get_error_knowledge`(错题知识点分布)、`get_score_trend`(得分率趋势)——均支持 `time_range` 自由文本（如"4月"）解析
- 生成类：`generate_analysis_report`(学情报告)、`generate_correction_workbook`(订正本)、`generate_study_plan`(学习计划)、`correct_composition`(作文批改)、`explain_exercise`(分步讲解)
- 知识状态：`update_knowledge_state`、`query_knowledge_state`、`record_mastery_feedback`(讲解反馈)

### 关键流程

**作业上传 → 手动切割 → 分析：**
1. `POST /api/v1/assignments` 接收文件 + `layout_type`，创建作业记录（状态 `pending`）
2. 前端通过 `GET /{id}/source-pages` 获取渲染后的页面图片，用户在画布上框选题目区域（ManualSplitModal）；大题支持框父区域后拆子题，客观题答题卡单独框选（AnswerSplitModal）
3. `POST /{id}/manual-split` 提交所有区域坐标，后端切图存入 MinIO/本地，状态变为 `splitted`
4. `POST /{id}/analyze` 触发异步分析（dev: `dev_runner` 后台任务，生产: Celery delay）
5. `analysis_tasks._do_analyze` 下载已有题目图片，调用多模态 LLM 逐题评分、识别答案、提取知识点、生成常见错误；大题先识别父题类型再拆子题
6. 分析结果写回 `assignment_questions` 各字段，更新 assignment 状态为 `completed`
7. 可靠性：DEV 模式启动时 `reconcile_all_stuck_assignments` 自愈卡在 grading 的作业；生产模式任务状态存 Redis（redis_state.py），前端轮询跨 worker 可见

**AI Agent 对话（SSE）：**
1. `POST /api/v1/ai-tutor/chat` → SSE StreamingResponse
2. 关键词路由（tool_router.py）：命中规则 → 只传工具子集 schema 进入 ReAct；未命中 → 不带 tools 直接流式回答（纯聊天秒回）
3. 每个 ReAct 循环（最多 5 轮）：LLM 决定调用工具或输出最终回答；整体时间预算 AGENT_TIME_BUDGET=240s
4. 工具调用通过 `AgentTools.execute()` 执行，结果回传 LLM
5. SSE 事件类型：`reasoning` / `tool_call` / `tool_result` / `token` / `error` / `done`
6. 对话多会话并行，消息落库（conversations/conversation_messages）；性格配置（agent_personality）注入提示词

**作文批改（异步）：**
1. `POST /api/v1/compositions` 建记录（status=pending）→ 触发 Celery/dev 后台任务批改
2. `composition_service` 按中高考官方标准评分（语文 60 分定档、英语五档制），字数后端硬统计，仅档外硬扣分记入 deductions
3. 前端轮询状态（pending→correcting→completed/failed），完成后可下载 PDF 报告

**AI 生成题流水线（question_pipeline）：**
- LangGraph StateGraph：`search`(联网检索, 未配 SEARCH_API_KEY 自动跳过) → `calibrate`(定难度) → `transform`(变式生成) → `verify`(校验, 不过则带 issues 回流 transform 重改，最多 max_attempts 轮)
- 用于 AI 挑战出题、上传题转录后生成等场景

**静态文件服务（仅 DEV 模式）：**
- `GET /api/v1/files/{file_path}` → `services/file_server.py` 从 `LOCAL_STORAGE_DIR` 读取
- 含路径穿越防护（resolve + prefix 校验）、私有目录鉴权（reports/、oral_audio/）、LLM 错误路径容错匹配

## 前端架构

```
frontend/src/
├── pages/
│   ├── Login/                        # 登录页（手机号+密码）
│   ├── AssignmentManagement/         # 作业管理板块
│   │   ├── UploadAssignment/         # 上传作业（含排版选择）
│   │   ├── AssignmentRecords/        # 作业记录列表（筛选 + 分页卡片）
│   │   ├── AssignmentDetail/         # 作业详情（总分 + AI 总评 + 题目卡片列表）
│   │   ├── ErrorRedo/                # 错题重做（筛选 + 错题卡片 + 同类题生成）
│   │   ├── AIChallenge/              # AI 挑战（AI 生成题作答）
│   │   └── MyFavorites/              # 我的收藏（收藏 + 上传自有试题）
│   ├── LearningAnalytics/            # 学情分析图表（@ant-design/charts）
│   ├── Composition/                  # 作文批改
│   ├── OralAssessment/               # 口语测评（听力/听写/普通话）
│   ├── Dashboard/                    # 数据看板（LLM 用量）
│   └── Settings/                     # 助教性格配置 / 账号管理
├── components/                       # 可复用组件
│   ├── Layout/                       # AppLayout（顶部导航）/ AssignmentLayout（侧边栏）
│   ├── UploadModal / ManualSplitModal / AnswerSplitModal   # 上传与切割
│   ├── QuestionCard / ErrorQuestionCard / SimilarQuestionCard / SimilarBigQuestionCard
│   ├── ExplainCard / AIQuestionHistoryCard / MathText / QuestionSvgImage / MarkdownPreview
│   ├── AuthedAudio / PlaybackRateControl                    # 听力音频
│   ├── AIFloatButton / ChatDrawer                           # 全局 AI 助手
│   └── ProtectedRoute / AppErrorBoundary / AnalysisProgressModal / AnalysisProgressPanel
├── services/                         # API 封装
│   ├── api.ts                        # axios 实例 + JWT 拦截
│   ├── tokenRefresher.ts             # 401 自动刷新令牌（队列化并发）
│   ├── authStorage.ts                # token 存储（单设备踢下线处理）
│   └── authService / userService / assignmentService / questionService / aiQuestionService /
│       aiTutorService(SSE) / analyticsService / errorQuestionService / favoriteService /
│       compositionService / oralService / conversationService / personalityService / usageService
├── hooks/                            # useAuth / useSSE / useUpload / useReanalysis
└── utils/  styles/
```

- **技术栈**: React 18 + TypeScript + Vite + Ant Design 5 + TanStack Query + @ant-design/charts
- **路由**: react-router-dom v6，`App.tsx` 定义全部路由
- **鉴权**: JWT access/refresh token，`api.ts` 拦截器自动附加 token + 401 自动刷新 + 队列化并发请求；全局 ChatHost 按 user.id 重建抽屉（防跨账号会话泄漏）
- **布局**: AppLayout（顶部导航） → AssignmentLayout（侧边栏） → 各子页面
- **全局 AI 助手**: AIFloatButton（悬浮按钮）+ ChatDrawer（聊天抽屉，SSE 流式对话）
- **代理**: Vite 开发服务器 `/api` → `http://localhost:8000`

## 数据库表

核心表（定义见 `backend/app/models/`）：

| 表 | 用途 |
|---|------|
| `users` | 用户，phone 登录账号（唯一）、username 显示名、role、token_version |
| `assignments` | 作业，含 layout_type, status, file_url, answer_sheet_image_url, ai_summary |
| `assignment_questions` | 单题，含 student_answer, correct_answer, score, knowledge_points(JSON), common_mistakes(JSON), parent_id/sub_question_index, bbox 坐标, question_text |
| `analysis_tasks` | 异步分析任务追踪 |
| `ai_generated_questions` | AI 生成的题目 + 上传转录题（source 区分），含 analysis/image_svg/group_id |
| `ai_question_answers` | AI 题目的作答记录 |
| `user_favorites` | 我的收藏（item_type: error/ai） |
| `conversations` / `conversation_messages` | Agent 会话与消息（含 reasoning、tool_calls） |
| `agent_personality` | 助教性格配置（每用户一行） |
| `user_knowledge_state` | 知识点掌握状态（mastery_score/level、正误次数） |
| `composition_corrections` | 作文批改记录（维度分、扣分明细、修改建议、status 状态机） |
| `listening_tests` / `dictation_tasks` / `mandarin_test_records` | 听力训练 / 单词听写 / 普通话测评 |
| `oral_records` | 口语测评统一作业记录（detail JSON + 冗余筛选列） |
| `llm_usage_logs` | LLM Token 用量日志（数据看板数据源） |

## 作业状态流转

```
pending → splitting → splitted → grading → completed
                                        ↓
                                     failed
```

（旧版 `processing` 状态兼容保留，启动时自动收敛为 `grading`）
