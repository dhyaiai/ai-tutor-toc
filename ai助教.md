# AI 助教系统 · 项目结构文档

## 1. 总体架构

| 层级     | 技术选型                                                     |
| -------- | ------------------------------------------------------------ |
| 前端     | React 18 + TypeScript + Vite + Ant Design 5 + TanStack Query + @ant-design/charts |
| 后端     | FastAPI (异步) + SQLAlchemy 2.0 + aiomysql                    |
| AI Agent | 大模型工具调用 Agent（关键词路由 → ReAct 循环，11 个工具）    |
| 认证     | JWT (python-jose) + bcrypt + 单设备登录 (token_version)       |
| 数据库   | MySQL 8.0（唯一数据库，无向量库）                             |
| 异步任务 | DEV 模式：进程内同步执行（dev_runner）；生产：Celery + Redis（任务状态存 Redis） |
| 文件存储 | MinIO (兼容 S3) 或本地静态目录（`./uploads`）                 |
| AI 能力  | DeepSeek 文本大模型（对话/讲解/批改）+ Qwen 视觉大模型（作业识别/评分/图片批改）+ 讯飞 ISE 语音评测（普通话）+ PaddleOCR/OpenCV（图像预处理）+ 浏览器 SpeechSynthesis（TTS） |
| 部署     | Windows 一键脚本（start_dev.bat / start_public.bat + Cloudflare Tunnel） |

## 2. 完整目录树

```
ai-tutor/
├── frontend/                # 前端工程
│   ├── src/
│   │   ├── components/      # 可复用组件
│   │   │   ├── Layout/      # AppLayout(顶部导航) / AssignmentLayout(侧边栏)
│   │   │   ├── UploadModal.tsx          # 作业上传模态框（含排版选择）
│   │   │   ├── ManualSplitModal.tsx     # 手动切割画布（框选题目区域）
│   │   │   ├── AnswerSplitModal.tsx     # 答案区域切割（客观题答题卡）
│   │   │   ├── QuestionCard.tsx         # 小题卡片（知识点/得分/重分析/确认）
│   │   │   ├── ErrorQuestionCard.tsx    # 错题卡片（作业来源/同类题生成）
│   │   │   ├── SimilarQuestionCard.tsx / SimilarBigQuestionCard.tsx  # 同类题卡片（小题/大题）
│   │   │   ├── AIFloatButton.tsx / ChatDrawer.tsx  # 全局 AI 助手（SSE 对话）
│   │   │   ├── ExplainCard.tsx          # 分步讲解卡片
│   │   │   ├── AIQuestionHistoryCard.tsx # AI 挑战作答历史
│   │   │   ├── MathText.tsx / QuestionSvgImage.tsx / MarkdownPreview.tsx  # 公式/配图/预览渲染
│   │   │   ├── AuthedAudio.tsx / PlaybackRateControl.tsx  # 听力音频播放（带鉴权/倍速）
│   │   │   ├── AnalysisProgressModal.tsx / AnalysisProgressPanel.tsx  # 分析进度展示
│   │   │   └── ProtectedRoute.tsx / AppErrorBoundary.tsx
│   │   ├── pages/
│   │   │   ├── Login/                   # 登录（手机号+密码）
│   │   │   ├── AssignmentManagement/    # 作业管理板块
│   │   │   │   ├── UploadAssignment/    # 上传作业
│   │   │   │   ├── AssignmentRecords/   # 作业记录（筛选+分页卡片）
│   │   │   │   ├── AssignmentDetail/    # 作业详情（总分+AI总评+题目卡片）
│   │   │   │   ├── ErrorRedo/           # 错题重做
│   │   │   │   ├── AIChallenge/         # AI 挑战（AI 生成题作答）
│   │   │   │   └── MyFavorites/         # 我的收藏（错题/AI题收藏 + 上传自有试题）
│   │   │   ├── LearningAnalytics/       # 学情分析（图表）
│   │   │   ├── Composition/             # 作文批改
│   │   │   ├── OralAssessment/          # 口语测评（听力/听写/普通话）
│   │   │   ├── Dashboard/               # 数据看板（LLM 用量统计）
│   │   │   └── Settings/                # 设置（助教性格配置 / 账号管理）
│   │   ├── services/                    # API 封装
│   │   │   ├── api.ts                   # axios 实例（JWT 拦截）
│   │   │   ├── tokenRefresher.ts        # 401 自动刷新令牌（队列化并发）
│   │   │   ├── authStorage.ts           # token 本地存储（含单设备踢下线处理）
│   │   │   ├── authService / userService / assignmentService / questionService /
│   │   │   ├── aiQuestionService / aiTutorService(SSE) / analyticsService /
│   │   │   ├── errorQuestionService / favoriteService / compositionService /
│   │   │   ├── oralService / conversationService / personalityService / usageService
│   │   ├── hooks/                       # useAuth / useUpload / useReanalysis / useSSE
│   │   ├── utils/ styles/  App.tsx  main.tsx
│   ├── package.json  vite.config.ts  tsconfig.json
│
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/          # 路由层（薄层）
│   │   │   ├── auth.py              # 登录、刷新令牌（注册已移除）
│   │   │   ├── users.py             # 用户管理（管理员开户）
│   │   │   ├── assignments.py       # 作业 CRUD、上传（layout_type）、手动切割、分析触发
│   │   │   ├── questions.py         # 单题重分析、确认、同类题生成、分步讲解
│   │   │   ├── ai_questions.py      # AI 生成题库（挑战/作答）
│   │   │   ├── upload_questions.py  # 上传自有试题（转录入库）
│   │   │   ├── favorites.py         # 我的收藏（错题/AI题）
│   │   │   ├── error_questions.py   # 错题查询
│   │   │   ├── analytics.py         # 学情聚合接口
│   │   │   ├── ai_tutor.py          # Agent SSE 对话入口 POST /api/v1/ai-tutor/chat
│   │   │   ├── conversations.py     # 会话管理（标题/消息/历史）
│   │   │   ├── personality.py       # 助教性格配置
│   │   │   ├── compositions.py      # 作文批改（异步状态机）
│   │   │   ├── oral_assessments.py  # 口语测评（听力/听写/普通话/记录列表）
│   │   │   └── usage_stats.py       # LLM 用量统计（数据看板）
│   │   ├── core/             # config.py / security.py / deps.py
│   │   ├── models/           # SQLAlchemy 2.0 模型（见第 3 节）
│   │   ├── schemas/          # Pydantic 请求/响应模型
│   │   ├── services/         # 业务逻辑层
│   │   │   ├── ai_grader.py            # 多模态 LLM 评分、答案识别（Qwen 视觉）
│   │   │   ├── image_preprocess.py     # 图像预处理（OCR/裁剪/压缩）
│   │   │   ├── question_transcriber.py # 题目转录（图片/PDF → 文本+LaTeX）
│   │   │   ├── pdf_renderer.py         # PDF 渲染（试卷转图片）
│   │   │   ├── similar_generator.py    # 同类题生成（大小题/组卷）
│   │   │   ├── knowledge_extractor.py  # 知识点提取
│   │   │   ├── knowledge_tracker.py    # 知识点掌握状态读写（user_knowledge_state）
│   │   │   ├── analytics_aggregator.py # 学情聚合计算
│   │   │   ├── composition_service.py  # 作文智能批改（中高考评分标准）
│   │   │   ├── oral_service.py         # 口语测评（听力/听写/普通话）
│   │   │   ├── xfyun_ise.py            # 讯飞流式语音评测（普通话朗读）
│   │   │   ├── explain_service.py      # 分步讲解
│   │   │   ├── personality_service.py  # 助教性格模板
│   │   │   ├── llm_usage_tracker.py    # LLM Token 用量全局追踪
│   │   │   ├── llm_json.py             # LLM JSON 请求统一封装（重试+容错解析）
│   │   │   ├── file_upload.py / file_server.py  # MinIO/本地存储与静态服务
│   │   │   ├── redis_state.py          # 生产模式任务状态 Redis 存储
│   │   │   ├── prompt_rules.py / remark_parser.py
│   │   │   └── agent/                  # AI Agent（工具路由 + ReAct）
│   │   │       ├── agent_executor.py   # ReAct 循环（LLM 思考→工具→结果→最终回答）
│   │   │       ├── tools.py            # 11 个工具（@tool 装饰器自动注册）
│   │   │       ├── tool_router.py      # 关键词路由层（命中→工具子集；未命中→纯聊天）
│   │   │       ├── route_config.py     # 路由规则表（关键词→工具）
│   │   │       └── prompts.py          # 系统提示词和模板
│   │   ├── tasks/            # 异步任务
│   │   │   ├── celery_app.py        # Celery 实例（生产模式）
│   │   │   ├── analysis_tasks.py    # 作业整体分析、单题重分析、卡死自愈
│   │   │   ├── composition_tasks.py # 作文批改异步任务
│   │   │   └── dev_runner.py        # DEV 模式同步执行器（asyncio 后台任务）
│   │   ├── db/               # session.py（异步引擎）+ base.py（模型聚合）
│   │   └── main.py           # FastAPI 入口：lifespan 建表/迁移/引导管理员、CORS/GZip、文件服务
│   ├── alembic/  requirements.txt  requirements-core.txt  tests/  uploads/
│
├── start_dev.bat             # 开发模式一键启动（pip install + 后端 + 前端）
├── start_public.bat          # 公网模式（MySQL 自启 + 后端 + 前端 + Cloudflare Tunnel）
├── CLAUDE.md                 # 开发者协作说明
└── ai助教.md                  # 本文档
```

## 3. 数据库核心表设计（MySQL，全部在 `backend/app/models/`）

### 3.1 用户与认证

- **users**：`id`, `phone`(登录账号, 唯一), `username`(显示名, 可空), `email`, `hashed_password`, `role`(USER/ADMIN，教师角色已移除), `token_version`(单设备登录版本号), `created_at`
- **agent_personality**：`user_id`(唯一), `template_name`, `personality_type`, `speaking_style`, `voice_tone`, `strict_level`（助教性格配置，每用户一行）

### 3.2 作业与题目

- **assignments**：`id`, `name`, `grade`, `subject`, `semester`, `usage_month`, `layout_type`(a4_single/a4_double/a3_double/a3_triple/a3_quad), `file_url`, `answer_sheet_image_url`(客观题答题卡), `ai_summary`, `total_score`, `status`(pending/splitting/splitted/grading/completed/failed), `creator_id`
- **assignment_questions**：`id`, `assignment_id`, `question_number`, `parent_id`/`sub_question_index`(大题套小题层级), `image_url`, `question_text`(识别题干, 含 `$...$` LaTeX), `student_answer`, `correct_answer`, `answer_image_url`, `score`, `full_score`, `analysis_detail`, `question_type`, `knowledge_points`(JSON), `common_mistakes`(JSON), `confidence_score`, `manual_review_note`, `page_index` + `bbox_x/y/w/h`(切割坐标), `status`
- **analysis_tasks**：`id`, `assignment_id`, `question_id`(nullable), `type`, `status`, `result_json`

### 3.3 AI 生成题库与收藏

- **ai_generated_questions**：`id`, `user_id`, `source_question_id`(来源原题, ON DELETE SET NULL), `group_id`/`sub_question_index`/`question_context`(大题分组), `question_text`, `answer`, `analysis`, `question_type`, `options`(JSON), `knowledge_point`, `difficulty`, `image_svg`/`context_image_svg`(配图), `source`(upload=上传转录 / NULL或ai=AI生成), `grade`/`subject`/`semester`(上传题自有元数据), `image_url`(原题图像), `created_at`
- **ai_question_answers**：AI 题作答记录（选项/答案/得分/用时）
- **user_favorites**：`user_id`, `item_type`(error=错题/ai=AI题), `question_id`（无跨表外键，应用层校验）

### 3.4 对话与知识状态

- **conversations**：`id`, `user_id`, `title`, `subject`, `status`(1正常/0删除), `created_at`, `updated_at`
- **conversation_messages**：`conversation_id`, `role`(user/assistant), `content`, `reasoning`(AI思考), `tool_calls`(JSON), `created_at`
- **user_knowledge_state**：`user_id`, `subject`, `point_name`, `mastery_score`, `mastery_level`, `wrong_count`, `correct_count`, `last_practice_time`（知识点掌握状态，跨会话持久化）

### 3.5 作文批改

- **composition_corrections**：`id`, `user_id`, `session_id`, `subject`, `title`, `total_score`, `full_score`, `word_count`(后端硬统计), `content`, `requirement`, `grade`, `essay_type`(读后续写/应用文/议论文等), `dimension_scores`(JSON), `deductions`(JSON扣分明细), `revision_suggestions`(JSON逐处修改), `overall_comment`, `polish_advice`, `sample_essay`, `strict_level`, `pdf_url`, `status`(pending/correcting/completed/failed), `error_message`

### 3.6 口语测评

- **listening_tests**：英语听力训练（题型/难度/得分）
- **dictation_tasks**：单词听写（词库范围/正确数/播放速度）
- **mandarin_test_records**：普通话测评（目标等级/分项得分/音频）
- **oral_records**：口语测评统一作业记录（category/name/score/detail JSON + detail_question_type/detail_word_scope/detail_direction/detail_difficulty 冗余筛选列 + grade_level）

### 3.7 用量统计

- **llm_usage_logs**：`model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `created_at`（LLM 调用全局追踪，启动时清理 90 天前旧数据）

## 4. 前端页面结构与交互说明

### 4.1 全局框架
- 登录（手机号+密码）后进入 `AppLayout`，顶部导航：作业管理 / 学情分析 / 作文批改 / 口语测评 / 数据看板 / 设置。
- 作业管理板块用 `AssignmentLayout` 侧边栏：上传作业、作业记录、错题重做、AI 挑战、我的收藏。
- 全局右下角 **AIFloatButton** + **ChatDrawer**（Agent SSE 对话），登录后所有板块可用；切换账号时抽屉按 user.id 重建，防止跨账号会话泄漏。

### 4.2 作业管理板块
- **上传作业**：UploadModal 填写作业名称/年级/科目，选择排版样式（A4单栏/A4双栏/A3双栏/A3三栏/A3四栏），上传文件。
- **手动切割**：上传后进入 ManualSplitModal，在渲染出的页面图片上框选题目区域（大题可框出父区域后拆分小题），也可框选答案区域（AnswerSplitModal，客观题答题卡）。提交后后端按坐标切图，状态变为 `splitted`。
- **作业记录**：筛选 + 分页卡片列表。
- **作业详情**：总分、AI 总评、题目卡片列表（图片、识别题干、答案、得分、知识点、分步讲解、重分析与确认按钮）；大题以父子层级展示，支持展开子题。
- **错题重做**：按年级/科目/学期/题型筛选 + 名称搜索，错题卡片显示来源、知识点、得分率，"AI生成同类题"按钮弹出 3 道同类题（支持大题组卷）。
- **AI 挑战**：从作业题目或知识点生成 AI 题作答，含作答历史、讲解反馈（反馈会更新知识状态）。
- **我的收藏**：收藏的错题/AI 题列表；可上传自有试题（图片/PDF → 转录为文本+LaTeX 入库，`source='upload'`），可编辑、删除、出处筛选。

### 4.3 其他板块
- **学情分析**：各类图表（作业提交数、平均分、趋势折线），年级/科目/月份筛选。
- **作文批改**：语文/英语作文（文本或图片），按中高考官方标准评分（语文 60 分：内容+表达+发展等级；英语应用文 15 / 读后续写 25，五档制），输出分项分、逐处修改建议、整体评价、润色方向、参考范文，可导出 PDF 报告；批改异步进行（pending→correcting→completed/failed），前端轮询状态。
- **口语测评**：英语听力训练（生成题目+批改）、单词听写（讯飞/LLM 批改）、普通话测评（朗读用讯飞 ISE 流式评测，其余 LLM 驱动）；TTS 用浏览器 SpeechSynthesis；统一作业记录列表（题型/难度筛选）。
- **数据看板**：LLM 用量统计（Token 消耗、模型分布），管理员视角。
- **设置**：助教性格配置（模板/风格/严格度，影响 Agent 语气）、账号管理。

## 5. 关键功能模块（后端视角）

### 5.1 AI 助手 (Agent 模式)
- 入口：`POST /api/v1/ai-tutor/chat`，SSE 流式返回，事件类型：`reasoning` / `tool_call` / `tool_result` / `token` / `error` / `done`。
- **关键词路由层**（tool_router.py）：进入 ReAct 前先用关键词规则把消息路由到工具子集——命中规则只传相关工具 schema（LLM 不能"凑数据"乱调）；未命中则不带 tools 直接流式回答（纯聊天秒回）；跨轮追问由 LLM 意图分类兜底（可配）。最坏结果是直接文本回答，避免此前"5 轮 × 120s 超时 = 卡 10 分钟"的问题。
- ReAct 循环（最多 5 轮）：LLM 思考 → 调工具 → 结果回传 → 最终回答，整体时间预算 `AGENT_TIME_BUDGET`=240s。
- 11 个工具（`tools.py`，@tool 装饰器自动注册 schema）：
  - 查询类：`get_assignment_score`(成绩统计)、`get_error_knowledge`(错题知识点分布)、`get_score_trend`(得分率趋势，均支持 time_range 自由文本解析)
  - 生成类：`generate_analysis_report`(学情报告)、`generate_correction_workbook`(订正本)、`generate_study_plan`(学习计划)、`correct_composition`(作文批改)、`explain_exercise`(分步讲解)
  - 知识状态：`update_knowledge_state`、`query_knowledge_state`、`record_mastery_feedback`(讲解反馈)
- 会话管理：对话可多会话并行，消息落库（conversations/conversation_messages）；助教性格配置（agent_personality）注入系统提示词。

### 5.2 作业上传 → 手动切割 → 分析
1. `POST /api/v1/assignments` 接收文件 + `layout_type`，创建作业记录（状态 `pending`）。
2. `GET /{id}/source-pages` 获取渲染后的页面图片（PDF 经 pdf_renderer 转图），用户在画布上框选题目/答案区域。
3. `POST /{id}/manual-split` 提交坐标，后端切图存入 MinIO/本地，状态 `splitted`。
4. `POST /{id}/analyze` 触发异步分析（dev: `dev_runner` 后台任务；生产: Celery delay）。
5. `analysis_tasks._do_analyze` 逐题调用视觉大模型评分、识别答案、提取知识点、生成常见错误；大题先识别父题类型再拆子题；分析结果写回 `assignment_questions`，作业状态 `completed`。
6. 可靠性：启动时 `reconcile_all_stuck_assignments` 自愈卡在 grading 的作业；生产模式任务状态存 Redis（redis_state.py）供多 worker 轮询。

### 5.3 作文批改
- 文本模式（直接传作文）/ 多模态模式（图片 base64 → 视觉 LLM 先识别再批改）。
- 评分严格按中高考官方标准（先定档再给分）；仅"档外硬扣分"（字数不足/缺标题/错别字/卷面）记入 deductions；字数由后端硬统计（排除标点）。
- 批改异步化：API 建记录（status=pending）→ Celery/dev 后台任务批改 → 完成后可下载 PDF 报告。

### 5.4 口语测评
- 英语听力训练：LLM 生成题目（短对话/长对话/短文理解/听写填空）+ 批改。
- 单词听写：按词库范围生成任务，图片批改用视觉模型，语音播报浏览器 TTS。
- 普通话测评：朗读模式走讯飞 ISE 流式语音评测（wss），其余 LLM 驱动，得分映射普通话等级。
- 所有记录统一写入 oral_records，题型/难度/方向冗余为独立列支持 SQL 筛选（旧数据启动时从 detail JSON 回填）。

### 5.5 知识状态追踪
- 批改完成、讲解反馈、作文批改、口语测评后自动更新 `user_knowledge_state`（mastery_score/level、正误次数），Agent 可查询/更新，跨会话持久化。

### 5.6 用户体系与安全
- 注册功能已移除，开户走管理员 `POST /users`；空库启动时自动引导创建初始管理员（FIRST_ADMIN_PHONE/PASSWORD，dev 随机密码打印控制台）。
- 单设备登录：token_version 校验，新登录踢掉旧设备（前端 401 后提示重新登录）。
- 静态文件服务（DEV 模式）：`/api/v1/files/{path}` 含路径穿越防护 + 私有目录鉴权（reports/oral_audio）+ 容错模糊匹配。

## 6. 运行说明

1. **开发模式**（DEV_MODE=true，本地存储+同步任务）：运行根目录 `start_dev.bat`，或手动：
   - 后端：`cd backend && C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe -m uvicorn app.main:app --reload --port 8000`
   - 前端：`cd frontend && npm run dev`（端口 5173，Vite 代理 `/api` → 8000）
   - 需本地 MySQL（库名 `ai_tutor`，启动时自动建表+幂等迁移）
2. **公网模式**：运行 `start_public.bat`（自启 MySQL 服务 + 后端 + 前端 + Cloudflare quick tunnel，每次新地址）。
3. **生产模式**（DEV_MODE=false）：需 MySQL + MinIO + Redis；Celery worker 执行异步任务；`SECRET_KEY`/`VISION_API_KEY`/`MINIO_PUBLIC_ENDPOINT`/`LLM_API_KEY` 缺失时启动失败。
4. API 文档：`http://localhost:8000/docs`；健康检查：`GET /health`。

## 7. 环境变量（backend/.env，见 config.py）

```ini
# 运行模式
DEV_MODE=true                      # true=开发(本地存储+同步任务)；false=生产
# 数据库（必须 MySQL）
DATABASE_URL=mysql+aiomysql://root:xxx@localhost:3306/ai_tutor
# Redis（仅生产模式异步任务状态用）
REDIS_URL=redis://localhost:6379/0
# MinIO（生产模式文件存储）
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=ai-tutor
MINIO_PUBLIC_ENDPOINT=localhost:9000   # 生产必须配置公网可访问地址
LOCAL_STORAGE_DIR=./uploads            # DEV 模式本地存储目录
# JWT
SECRET_KEY=your-secret-key             # 生产必须配置，长度>=16
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7
# 文本大模型（DeepSeek：对话/讲解/批改/知识点等）
LLM_API_KEY=sk-xxxx
LLM_API_BASE=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
# 视觉大模型（Qwen：作业识别/评分/图片批改）
VISION_API_KEY=sk-xxxx
VISION_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
VISION_MODEL=qwen3.7-plus
# 首启引导管理员（注册已移除，空库自动创建）
FIRST_ADMIN_PHONE=test
FIRST_ADMIN_PASSWORD=
# 单设备登录
SINGLE_DEVICE_LOGIN=true
# Agent 可靠性
AGENT_TIME_BUDGET=240
TOOL_EXEC_TIMEOUT=60
ROUTE_ENABLED=true
# 讯飞语音评测（普通话朗读）
XFYUN_APP_ID=
XFYUN_API_KEY=
XFYUN_API_SECRET=
XFYUN_ISE_URL=wss://ise-api.xfyun.cn/v2/open-ise
# CORS
CORS_ORIGINS=
# 上传限制
MAX_UPLOAD_SIZE_MB=50
```
