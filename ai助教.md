# AI 助教系统 · 项目结构文档

## 1. 总体架构

| 层级     | 技术选型                                                     |
| -------- | ------------------------------------------------------------ |
| 前端     | React 18 + TypeScript + Vite + Ant Design 5 + TanStack Query + @ant-design/charts |
| 后端     | FastAPI (异步) + SQLAlchemy 2.0 + Celery + Redis             |
| AI Agent | 基于大模型的工具调用 Agent (Tools: 向量检索、学情统计、错题分析等) |
| 认证     | JWT (python-jose) + bcrypt + 限流 (slowapi)                  |
| 数据库   | MySQL 8.0 (业务数据) + Milvus/Qdrant (向量检索)              |
| 异步任务 | Celery Worker (AI 分析、向量化、同类题生成)                  |
| 文件存储 | MinIO (兼容 S3) 或本地静态目录                               |
| AI 能力  | PaddleOCR + OpenCV (题号检测与切割，支持按排版分栏) + 多模态大模型 API (识别/评分/知识点/同类题) + 文本嵌入模型 (向量化) |
| 部署     | Docker Compose 一体化编排                                    |

## 2. 完整目录树

ai-tutor/
├── frontend/ # 前端工程
│ ├── public/
│ │ └── favicon.ico
│ ├── src/
│ │ ├── assets/ # 静态资源（图标、图片等）
│ │ ├── components/ # 可复用组件
│ │ │ ├── Layout/
│ │ │ │ ├── AppLayout.tsx # 应用主布局 (顶部导航+内容区)
│ │ │ │ ├── Header.tsx # 顶部导航栏 (作业管理/学情分析切换)
│ │ │ │ └── AssignmentLayout.tsx # 作业管理板块子布局 (侧边栏+内容)
│ │ │ ├── UploadModal.tsx # 作业上传模态框（含排版选择）
│ │ │ ├── QuestionCard.tsx # 小题卡片（含知识点、得分、重新生成/确认按钮）
│ │ │ ├── ErrorQuestionCard.tsx # 错题卡片（含作业来源、生成同类题按钮）
│ │ │ ├── SimilarQuestionsPopover.tsx # 同类题弹出卡片
│ │ │ ├── AIFloatButton.tsx # 悬浮AI助手按钮 (全局)
│ │ │ ├── ChatDrawer.tsx # 聊天抽屉界面 (Agent对话)
│ │ │ └── ProtectedRoute.tsx # 登录鉴权路由守卫
│ │ ├── pages/ # 页面组件
│ │ │ ├── Login/
│ │ │ │ └── index.tsx
│ │ │ ├── AssignmentManagement/ # 作业管理板块 (包含三个子功能)
│ │ │ │ ├── UploadAssignment/ # 上传作业（使用UploadModal）
│ │ │ │ │ └── index.tsx
│ │ │ │ ├── AssignmentRecords/ # 作业记录列表
│ │ │ │ │ └── index.tsx
│ │ │ │ ├── AssignmentDetail/ # 作业详情（题目列表+整体分析）
│ │ │ │ │ └── index.tsx
│ │ │ │ └── ErrorRedo/ # 错题重做 (筛选+卡片列表)
│ │ │ │ └── index.tsx
│ │ │ └── LearningAnalytics/ # 学情分析板块 (图表页)
│ │ │ └── index.tsx
│ │ ├── services/ # API 请求封装
│ │ │ ├── api.ts # axios 实例 (带 JWT 拦截)
│ │ │ ├── authService.ts
│ │ │ ├── assignmentService.ts
│ │ │ ├── questionService.ts
│ │ │ ├── analyticsService.ts
│ │ │ ├── aiTutorService.ts # Agent 对话接口 (SSE)
│ │ │ └── errorQuestionService.ts
│ │ ├── hooks/ # 自定义 hooks
│ │ │ ├── useAuth.ts
│ │ │ ├── useUpload.ts
│ │ │ ├── useReanalysis.ts
│ │ │ └── useSSE.ts # SSE 流式对话
│ │ ├── utils/
│ │ │ ├── constants.ts
│ │ │ └── helpers.ts
│ │ ├── App.tsx
│ │ └── main.tsx
│ ├── package.json
│ ├── vite.config.ts
│ ├── tsconfig.json
│ └── .env.example
│
├── backend/ # FastAPI 后端
│ ├── app/
│ │ ├── api/ # 路由
│ │ │ └── v1/
│ │ │ ├── **init**.py
│ │ │ ├── auth.py # 登录、注册、刷新令牌
│ │ │ ├── assignments.py # 作业 CRUD、上传（接收 layout_type）
│ │ │ ├── questions.py # 单题重分析、确认、生成同类题
│ │ │ ├── analytics.py # 学情聚合数据
│ │ │ ├── ai_tutor.py # Agent 对话入口 (SSE)
│ │ │ └── error_questions.py # 错题查询
│ │ ├── core/ # 核心配置与安全
│ │ │ ├── config.py # 环境变量读取
│ │ │ ├── security.py # JWT、密码哈希、限流依赖
│ │ │ └── deps.py # 通用依赖 (get_db, get_current_user)
│ │ ├── models/ # SQLAlchemy 模型
│ │ │ ├── **init**.py
│ │ │ ├── user.py
│ │ │ ├── assignment.py
│ │ │ └── question.py
│ │ ├── schemas/ # Pydantic 校验模型
│ │ │ ├── user.py
│ │ │ ├── assignment.py
│ │ │ ├── question.py
│ │ │ ├── analytics.py
│ │ │ └── ai.py
│ │ ├── services/ # 业务逻辑层
│ │ │ ├── file_upload.py # MinIO/本地存储操作
│ │ │ ├── ocr_splitter.py # 题目切割（根据 layout_type 执行分栏策略）
│ │ │ ├── ai_grader.py # 多模态大模型调用 (评分、答案生成)
│ │ │ ├── similar_generator.py # 同类题生成
│ │ │ ├── knowledge_extractor.py # 知识点提取
│ │ │ ├── rag_service.py # 向量存储与检索 (底层工具)
│ │ │ ├── analytics_aggregator.py # 学情聚合计算
│ │ │ └── agent/ # AI Agent 模块
│ │ │ ├── **init**.py
│ │ │ ├── agent_executor.py # Agent 执行器 (ReAct/LangChain)
│ │ │ ├── tools.py # Agent 工具函数定义
│ │ │ └── prompts.py # Agent 系统提示词
│ │ ├── tasks/ # Celery 异步任务
│ │ │ ├── **init**.py
│ │ │ ├── celery_app.py # Celery 实例定义
│ │ │ ├── analysis_tasks.py # 作业整体分析、单题重分析
│ │ │ └── vector_tasks.py # 向量化入库
│ │ ├── db/ # 数据库相关
│ │ │ ├── session.py # 异步 session 工厂
│ │ │ └── base.py # 基础模型类
│ │ ├── main.py # FastAPI 应用入口
│ │ └── utils/ # 工具函数
│ ├── alembic/ # 数据库迁移文件
│ │ ├── env.py
│ │ └── versions/
│ ├── requirements.txt
│ └── Dockerfile
│
├── docker-compose.yml # 一体化部署 (MySQL, Redis, MinIO, Milvus, Backend, Worker, Frontend)
├── .env.example # 环境变量模板
└── README.md

## 3. 数据库核心表设计

### 3.1 MySQL 表

- **users**  
  `id`, `username`, `email`, `hashed_password`, `role`, `created_at`

- **assignments**  
  `id`, `name`, `grade`, `subject`, `semester`, `month`, `layout_type` (排版类型，如 `a4_single`, `a4_double`, `a3_double`, `a3_triple`, `a3_quad`), `file_url`, `ai_summary`, `total_score`, `status`, `creator_id`, `created_at`

- **assignment_questions**  
  `id`, `assignment_id`, `question_number`, `image_url`, `student_answer`, `correct_answer`, `score`, `full_score`, `analysis_detail`, `knowledge_points`(JSON), `status`, `created_at`

- **analysis_tasks**  
  `id`, `assignment_id`, `question_id`(nullable), `type`, `status`, `result_json`, `created_at`

### 3.2 向量数据库 (Milvus / Qdrant)

- **Collection `analysis_chunks`**  
  - `chunk_id` (主键)  
  - `text` (分析文本)  
  - `embedding` (向量)  
  - `metadata` (JSON: `assignment_id`, `semester`, `month`, `grade`, `subject`, `question_id`...)

## 4. 前端页面结构与交互说明

### 4.1 全局框架
- 登录成功后进入 `AppLayout`，顶部固定 **Header**，提供「作业管理」和「学情分析」两个板块的切换标签。
- 全局右下角悬浮 **AIFloatButton**，点击打开 **ChatDrawer**（与 Agent 对话），无论在哪个板块均可使用。

### 4.2 作业管理板块
- 采用 `AssignmentLayout`，左侧显示侧边栏，包含三个导航项：
  - **上传作业** (UploadAssignment)
  - **作业记录** (AssignmentRecords)
  - **错题重做** (ErrorRedo)
- 点击侧边栏项切换右侧内容区：
  - **上传作业**：点击后弹窗,支持拖拽上传的点击上传,需填写作业名称、年级、科目，**并选择排版样式（A4单栏、A4双栏、A3双栏、A3三栏、A3四栏）**，然后上传文件。提交后后端将根据所选排版执行相应的切割逻辑。
  - **作业记录**：右侧内容区上面显示筛选项,下面显示每条作业记录,每页十条,每条记录以卡片的形式展现。
  - **作业详情**：点击作业记录里的作业后进入作业详情,显示总分、AI 整体分析，以及每道题目的卡片列表（图片、答案、得分、知识点、重新生成与确认按钮）。
  - **错题重做**：支持按年级/科目/学期筛选，作业名称模糊搜索，筛选和作业名称模糊搜索功能在上方，下面展示错题卡片，卡片下方显示本题的作业来源、知识点、得分率：本题得分/本题分值，每题的右下角有“AI生成同类题”按钮，点击后弹出小卡片，显示 3 道同类题。

### 4.3 学情分析板块
- 直接展示各类图表（作业提交份数、各科平均分、数学分数趋势折线图），页面内嵌下拉筛选条件（年级、科目、月份）。
- AI 助手同样可以通过悬浮按钮唤起，进行针对性提问（如“分析三月数学薄弱点、数学在后面应该学习”），Agent 会调用工具获取数据并生成总结。

## 5. 关键功能模块（后端视角）

### 5.1 AI 助手 (Agent 模式)
- 入口：`POST /api/v1/ai-tutor/chat`，通过 SSE 流式返回。
- Agent 可调用以下工具（定义于 `agent/tools.py`）：
  - `search_analysis_chunks`：向量检索作业分析文本。
  - `get_assignment_score`：获取平均分、提交数量等统计数据。
  - `get_error_knowledge`：查询错题知识点分布。
  - `get_score_trend`：获取分数趋势数据。
- 工具层复用 `rag_service.py` 和 `analytics_aggregator.py`，保持服务解耦。

### 5.2 作业上传与分析（含排版切割）
- 上传接口 `POST /api/v1/assignments` 接收文件及 `layout_type` 参数（五个固定值之一）。
- 后端 `ocr_splitter.py` 根据 `layout_type` 执行不同切割策略：
  - **A4单栏**：直接对整页进行 Y 轴题号检测与切割。
  - **A4双栏 / A3双栏**：先用 OpenCV 投影法或 PaddleLayout 分出左右两栏，再在每栏内部独立切割题目，最终按“左栏从上到下→右栏从上到下”的顺序拼接题目。
  - **A3三栏 / A3四栏**：同理，先分割为三或四栏，再逐栏切割并按顺序组织题目。
- 切割后的单题图片存入 MinIO/本地，同时在数据库创建 `assignment_questions` 记录。
- 异步任务随后调用多模态大模型进行评分、答案识别、知识点提取，并将分析文本向量化存入 Milvus。

### 5.3 错题重做与同类题生成
- 错题筛选接口 `GET /api/v1/error-questions`，支持多条件过滤。
- 同类题生成接口 `POST /api/v1/questions/{qid}/similar`，调用大模型生成 3 道同类题并返回。

## 6. 运行说明 (开发环境)

1. **启动基础设施**  
   `docker-compose up -d mysql redis minio milvus`

2. **后端**  
   `cd backend && cp .env.example .env` (配置后)  
   `uvicorn app.main:app --reload`

3. **Celery Worker**  
   `cd backend && celery -A app.tasks.celery_app worker --loglevel=info`

4. **前端**  
   `cd frontend && npm install && npm run dev`

## 7. 环境变量示例 (.env.example)

```ini
# 数据库
DATABASE_URL=mysql+asyncmy://user:pass@localhost:3306/ai_tutor
# Redis
REDIS_URL=redis://localhost:6379/0
# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
# JWT
SECRET_KEY=your-secret-key-keep-it-safe
# AI 大模型
LLM_API_KEY=sk-xxxxxxxx
LLM_API_BASE=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-small
# 向量数据库
MILVUS_HOST=localhost
MILVUS_PORT=19530