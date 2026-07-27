# AI 助教 Agent 系统 开发规格文档（Vibe Coding 版）

> 本文档为可直接落地开发的工程化规格说明，覆盖全部需求的工具定义、提示词、数据结构、交互流程与技术规范，适配 Python + FastAPI + LangChain 技术栈，可直接用于 Vibe Coding 快速搭建。 本次更新：新增口语测评、作文批改两大独立功能板块；完善语音实时交互体系；新增助教全维度预设模板；细化批改评分尺度与修改建议规则。

## 一、项目概述

### 1.1 产品定位

面向 C 端用户的网页版 AI 助教系统，以 Agent 智能编排为核心，覆盖作业分析、错题订正、题目讲解、口语测评、作文批改、学情追踪六大核心板块，支持多会话管理、长期记忆沉淀、全功能自然语言操控与实时语音交互。

### 1.2 核心功能清单

表格

| 大模块      | 子功能点                                                     |
| ----------- | ------------------------------------------------------------ |
| 作业分析    | 单作业 / 周期汇总分析报告生成，支持 PDF 导出                 |
| 错题订正    | 单作业 / 周期错题订正本生成，支持 PDF 导出                   |
| AI 助教交互 | 语音题目讲解、可切换性格系统、交互式答疑主动追问、实时语音对话 |
| 口语测评    | 英语：考试听力题训练、单词听写测评；语文：古诗文默写提问、普通话水平测试 |
| 作文批改    | 语文 / 英语作文智能批改、逐处修改建议、润色评分、参考范文，支持 PDF 导出 |
| 学情追踪    | 跨会话全学科知识点掌握状态追踪，动态更新与加载，覆盖作业 / 口语 / 作文全场景 |

### 1.3 技术栈约定

- 前端：React 18 + TypeScript + Ant Design 5 + pdf.js + 浏览器 MediaRecorder 流式录音 API + Web Audio API
- 后端：FastAPI（异步） + LangChain + Celery 异步任务 + 流式 ASR/TTS 服务
- AI 能力：支持 Function Calling 的大模型 + PaddleOCR + 流式语音识别 / 合成 + 向量数据库
- 存储：MySQL 8.0（业务数据）+ Redis（会话缓存 + 实时语音流）+ 对象存储 OSS（文件 / PDF / 音频）
- PDF 渲染：WeasyPrint（HTML 模板转 PDF）
- 实时语音：分片流式传输 + 边录边转写 + 流式 TTS 播放，实现低延迟对话

### 1.4 功能入口与载体映射

三大前端入口统一复用 Agent 核心能力，用户状态与会话全局打通；口语、作文为独立功能板块，同时支持聊天抽屉自然语言唤起。

表格

| 功能模块              | 前端入口                   | 承载组件                   | 交互形式                            |
| --------------------- | -------------------------- | -------------------------- | ----------------------------------- |
| 作业报告 / 订正本生成 | 全局悬浮 AI 聊天抽屉       | ChatDrawer                 | 自然语言指令触发，返回 PDF          |
| 跨会话学情状态查询    | 全局悬浮 AI 聊天抽屉       | ChatDrawer                 | 自然语言提问，返回学情总结          |
| 助教性格与评分配置    | 顶部导航用户账号入口       | 用户设置 - 助教配置页      | 预设模板一键切换 + 自定义微调       |
| 单题分步讲解          | 单道题目卡片独立按钮       | QuestionCard + ChatDrawer  | 一键触发，双端内容同步              |
| 口语测评全功能        | 主导航「口语测评」独立板块 | 口语测评页面（4 个子模块） | 页面化交互 + 实时语音流             |
| 作文批改全功能        | 主导航「作文批改」独立板块 | 作文批改页面（双学科）     | 文本 / 图片 / 语音多输入 + 逐处批改 |

------

## 二、整体系统架构

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────┐
│  前端Web层：会话管理 | 聊天交互 | 文件上传 | PDF预览  │
│  实时语音录制播放 | 性格设置 | 学情看板 | 口语测评页  │
│  作文批改页 | 单题讲解入口                           │
├─────────────────────────────────────────────────────┤
│  API网关层：鉴权 | 限流 | 路由 | 文件上传预处理       │
│  实时语音网关：ASR流接入 | TTS流下发                 │
├─────────────────────────────────────────────────────┤
│  Agent编排层：LLM推理 | 工具调度 | 记忆管理 | RAG检索 │
│  评分尺度控制：严格等级全局生效                      │
├─────────────────────────────────────────────────────┤
│  能力服务层：作业解析 | 报告生成 | 口语测评 | 作文批改 │
│         PDF渲染 | 流式语音合成/识别 | 知识点图谱      │
├─────────────────────────────────────────────────────┤
│  数据存储层：MySQL | Redis | 向量库 | OSS对象存储     │
└─────────────────────────────────────────────────────┘
```

### 2.2 Agent 核心工作流

1. 用户触发交互（文本 / 语音 / 文件 / 页面按钮点击）→ 网关鉴权 → 进入 Agent 编排层
2. Agent 加载：全局系统提示词 + 用户预设模板配置 + 长期知识状态 + 当前会话记忆
3. 意图识别：自然语言输入由 LLM 判断需求匹配工具；按钮 / 页面触发事件直接定向调用对应工具
4. 工具执行：调用对应能力服务，按当前评分严格度生成结果
5. 结果整合：LLM 将工具返回结果转化为自然语言回复，语音场景同步生成流式 TTS
6. 状态更新：同步更新会话记忆 + 知识状态 + 业务数据
7. 返回前端：文本 / 音频流 / PDF 下载链接，按入口适配展示格式

### 2.3 实时语音交互链路

1. 前端调用浏览器麦克风，分片采集音频（每片 200ms），通过 WebSocket 流式上传
2. 后端 ASR 服务边接收边转写，实时返回识别文本至前端
3. 完整语句送入 Agent 编排层，生成回复文本的同时启动流式 TTS
4. 前端边接收音频流边播放，实现低延迟实时对话，支持打断、重说
5. 语音对话内容自动同步写入会话历史，与文本消息统一管理

------

## 三、Agent 全局底座设计

### 3.1 全局系统提示词（完整版）

> 每次对话必注入，可直接作为 system prompt 使用

```
你是用户的专属AI学习助教，专注于提供专业、耐心的学科辅导服务。所有回复必须严格遵循以下规则。
【身份与底线】
1. 仅回答学习相关问题，拒绝无关内容，禁止输出违规、低俗、非教育类信息。
2. 所有专业内容必须结合RAG知识库校验，确保知识点准确，禁止编造答案。
3. 永远保持当前设定的助教性格与评分尺度，语气风格、打分标准统一，不得偏离。
【记忆规则】
1. 你拥有用户的长期知识状态：{{user_knowledge_state}}
   - 讲解难度、出题难度、评分松紧必须匹配用户当前掌握等级
   - 对薄弱知识点优先关注、主动提醒、重点讲解
2. 当前会话历史：{{conversation_history}}
   - 结合上下文对话，避免重复提问，保持话题连贯性
   - 题目卡片触发的讲解、口语测评记录、作文批改内容均已纳入会话历史
【交互规则（交互式答疑）】
1. 讲解题目必须分步推进，每讲完一个核心步骤，主动追问用户理解情况。
2. 用户反馈听懂，记录掌握度并可推荐同类练习；用户反馈没听懂，立即更换讲解角度，拆解更细步骤。
3. 禁止一次性输出全部解题过程，禁止直接甩答案，引导学生自主思考。
4. 实时语音对话使用短句、口语化表达，避免长难句和复杂符号，语速匹配当前性格设定。
【工具使用规则】
1. 涉及作业分析、订正本生成、口语测评、作文批改、知识状态更新等业务，必须调用对应工具完成，禁止凭空编造结果。
2. 工具调用参数必须从用户对话或前端事件中准确提取，缺失参数可向用户确认。
3. 工具执行完成后，将核心结果转化为自然语言回复，PDF/音频文件直接给出下载/播放入口。
【评分尺度规则】
当前评分严格等级：{{strict_level}}/5
- 等级越高，作业、作文、口语评分越严格，扣分标准越细，错误指出越犀利
- 等级越低，评分越宽松，鼓励性内容越多，重点标注核心错误
所有批改、测评、打分必须严格遵循当前严格等级。
【性格设定】
当前助教性格：{{personality_type}}
说话风格：{{speaking_style}}
语音音色：{{voice_tone}}
所有回复的语气、措辞、节奏必须严格贴合性格设定。
```

### 3.2 记忆系统设计

#### 3.2.1 短期会话记忆

- 存储内容：当前会话对话历史、中间文件 ID、生成的报告 / 作业 ID、单题讲解记录、口语测评片段、作文草稿
- 实现方式：Redis 缓存（会话活跃期）+ MySQL 持久化
- 截断策略：最近 15 轮对话 + 早期对话摘要，控制 token 长度
- 全局复用规则：每个用户维护一个全局活跃会话 ID，所有功能入口共享同一会话上下文

#### 3.2.2 长期知识状态记忆（跨会话）

- 存储内容：用户全学科知识点掌握画像、错题统计、口语能力维度、作文能力维度、学习行为数据
- 生命周期：跨会话永久有效，随所有学习行为动态更新
- 加载时机：新会话创建时自动注入系统提示词
- 覆盖场景：作业批改、题目讲解、口语测评、作文批改、错题订正

### 3.3 工具调用总规则

- 所有工具遵循 OpenAI Function Calling 规范
- 支持两种触发模式：
  1. **智能调度模式**：聊天抽屉自然语言输入，由 LLM 识别意图并自动匹配工具
  2. **定向调用模式**：前端按钮 / 页面事件触发，无需意图识别，直接传入参数调用指定工具
- 支持单轮多工具并行调用（如同时生成报告和订正本）
- 耗时任务（PDF 生成、批量分析、口语批量测评）异步执行，前端轮询进度
- 所有批改 / 测评类工具必须接收 `strict_level` 参数，控制评分尺度

### 3.4 助教预设模板体系

#### 3.4.1 预设模板总览

提供 4 套官方预设模板，一键应用，覆盖性格、说话风格、语音音色、评分严格度全维度；支持用户在预设基础上自定义微调。

表格

| 预设模板名称 | 核心定位                | 性格类型   | 说话风格   | 语音音色   | 评分严格度 | 适用人群                |
| ------------ | ----------------------- | ---------- | ---------- | ---------- | ---------- | ----------------------- |
| 温柔鼓励型   | 基础薄弱 / 低龄学生适配 | 温柔鼓励型 | 口语化亲切 | 温柔女声   | 2/5        | 小学 / 初中基础薄弱学生 |
| 严谨专业型   | 通用标准学习场景        | 严谨专业型 | 书面化正式 | 沉稳男声   | 3/5        | 全年龄段通用，默认选项  |
| 幽默活泼型   | 缓解学习压力，提升兴趣  | 幽默活泼型 | 口语化亲切 | 活泼童声   | 2/5        | 低龄学生、兴趣培养阶段  |
| 严格督学型   | 拔高冲刺，高标准要求    | 严格督学型 | 简洁高效   | 磁性青年音 | 5/5        | 高年级、备考冲刺阶段    |

#### 3.4.2 评分严格度分级规则

严格等级全局生效，覆盖作业批改、作文评分、口语测评、错题订正所有打分场景。

表格

| 严格等级 | 打分尺度说明                                                 |
| -------- | ------------------------------------------------------------ |
| 1 分     | 极宽松，仅标注核心错误，小失误不扣分，评语以鼓励为主，容错率高 |
| 2 分     | 偏宽松，重点错误扣分，细节失误酌情扣分，鼓励多于批评         |
| 3 分     | 标准适中，按常规考试评分标准打分，错误与鼓励均衡，客观公正   |
| 4 分     | 偏严格，细节错误均扣分，错因标注细致，评语要求明确，整改建议具体 |
| 5 分     | 极严格，对标升学考试评分标准，抠细节、高标准，错误一针见血，要求高、提升导向 |

#### 3.4.3 模板配置生效链路

1. 入口：顶部导航点击用户头像 → 下拉菜单选择「助教设置」→ 进入配置页
2. 页面上方展示 4 套预设模板卡片，点击「应用模板」一键填充所有配置
3. 下方提供自定义微调项，用户可单独修改性格、音色、严格度等参数
4. 提交保存后，写入 `agent_personality` 表，同时更新 Redis 用户配置缓存
5. 实时生效：当前活跃会话自动热更新系统提示词，无需重启会话
6. 聊天抽屉头部展示当前使用的模板名称，点击可跳转至设置页

------

## 四、核心功能模块详细设计

### 模块 1：作业分析报告与订正本 PDF 生成

#### 1.1 功能说明

- 支持两种模式：单作业分析、周期汇总分析
- 支持两种产物单独生成：分析报告 PDF、错题订正本 PDF
- 评分标准跟随全局严格等级动态调整
- 统一入口：全局悬浮聊天抽屉，支持快捷指令一键触发

#### 1.2 Function Calling 工具定义

##### 工具 1：生成作业分析报告 `generate_analysis_report`

```
{
  "name": "generate_analysis_report",
  "description": "生成作业学情分析报告，支持单作业分析与指定时间范围的汇总分析，自动生成PDF文件并返回下载链接。",
  "parameters": {
    "type": "object",
    "properties": {
      "mode": {
        "type": "string",
        "enum": ["single", "summary"],
        "description": "必填。single=单作业分析；summary=周期汇总分析"
      },
      "homework_file_id": {
        "type": "string",
        "description": "单作业模式必填。上传的作业文件ID"
      },
      "time_range": {
        "type": "string",
        "description": "汇总模式必填。时间范围，如「最近30天」「2024-05」"
      },
      "subject": {
        "type": "string",
        "enum": ["语文", "数学", "英语", "物理", "化学", "生物", "历史", "地理", "政治"],
        "description": "必填。作业所属学科"
      },
      "grade": {
        "type": "string",
        "description": "可选。对应年级"
      },
      "report_depth": {
        "type": "string",
        "enum": ["精简版", "标准版", "详细版"],
        "default": "标准版"
      },
      "strict_level": {
        "type": "integer",
        "minimum": 1,
        "maximum": 5,
        "description": "评分严格等级，默认读取用户全局配置"
      },
      "generate_pdf": {
        "type": "boolean",
        "default": true
      }
    },
    "required": ["mode", "subject"]
  }
}
```

> 工具返回值：`{ "report_id": "xxx", "pdf_url": "xxx", "file_name": "xxx.pdf", "summary": "报告核心结论", "correct_rate": 0.85 }`

##### 工具 2：生成错题订正本 `generate_correction_workbook`

```
{
  "name": "generate_correction_workbook",
  "description": "生成错题订正本，支持基于单份报告或指定时间范围的错题汇总，自动生成PDF并返回下载链接。",
  "parameters": {
    "type": "object",
    "properties": {
      "mode": {
        "type": "string",
        "enum": ["single", "summary"],
        "description": "必填。single=单作业错题；summary=周期错题汇总"
      },
      "report_id": {
        "type": "string",
        "description": "单作业模式必填。对应作业分析报告ID"
      },
      "time_range": {
        "type": "string",
        "description": "汇总模式必填。时间范围"
      },
      "subject": {
        "type": "string",
        "description": "汇总模式必填。筛选学科"
      },
      "workbook_type": {
        "type": "string",
        "enum": ["标准订正版", "错题重练版"],
        "default": "标准订正版"
      },
      "include_knowledge_hint": {
        "type": "boolean",
        "default": false
      },
      "include_full_solution": {
        "type": "boolean",
        "default": false
      },
      "sort_by": {
        "type": "string",
        "enum": ["知识点归类", "错题顺序", "错误严重程度"],
        "default": "知识点归类"
      },
      "generate_pdf": {
        "type": "boolean",
        "default": true
      }
    },
    "required": ["mode"]
  }
}
```

> 工具返回值：`{ "workbook_id": "xxx", "pdf_url": "xxx", "file_name": "xxx.pdf", "question_count": 0 }`

#### 1.3 专属系统提示词

```
你现在需要生成专业的作业学情分析报告，严格遵循以下规则：
1. 输出结构化JSON数据，供PDF渲染使用，禁止纯文本。
2. 知识点标注必须与学科知识库一致，错因限定为：概念模糊、计算失误、审题偏差、方法未掌握、综合能力不足。
3. 评分严格遵循当前strict_level等级：等级越高，扣分越严，错因分类越细。
4. 汇总报告必须包含：整体正确率趋势、知识点掌握变化、高频错题、进步点、待提升点。
5. 报告生成完成后，必须自动调用 update_knowledge_state 工具更新用户知识状态。
6. 返回给聊天窗口的摘要控制在200字以内，突出核心结论与薄弱点。
```

#### 1.4 业务执行流程

1. 用户在聊天抽屉输入指令
2. Agent 提取参数，缺失时主动追问，自动带入用户全局严格等级
3. 并行 / 串行调用对应工具，异步任务执行
4. 聊天窗口实时展示任务进度，生成完成后返回 PDF 下载卡片
5. 自动触发知识状态更新，用户可直接追问薄弱知识点

------

### 模块 2：AI 助教交互与题目讲解

#### 2.1 子模块 A：单题分步讲解（卡片 + 聊天双端）

##### 功能说明

支持双触发模式：聊天窗口自然语言提问、单题卡片一键讲解；讲解遵循交互式分步规则，支持文本 / 语音双输出，自动关联知识状态更新。

##### 工具定义 `explain_exercise`

```
{
  "name": "explain_exercise",
  "description": "针对单道题目提供分步讲解，支持文本/语音输出，自动关联知识点与用户学情。",
  "parameters": {
    "type": "object",
    "properties": {
      "exercise_content": {
        "type": "string",
        "description": "必填。题目完整题干"
      },
      "subject": {
        "type": "string",
        "description": "题目所属学科"
      },
      "question_id": {
        "type": "string",
        "description": "可选。题目记录ID，用于关联错题与知识状态更新"
      },
      "explanation_style": {
        "type": "string",
        "enum": ["分步引导式", "直接讲解式", "基础科普式"],
        "default": "分步引导式"
      },
      "output_format": {
        "type": "string",
        "enum": ["text", "audio", "stream_audio"],
        "default": "text",
        "description": "输出形式：文本/整段音频/流式实时语音"
      },
      "voice_type": {
        "type": "string",
        "default": "{{voice_tone}}"
      },
      "card_mode": {
        "type": "boolean",
        "default": false,
        "description": "卡片模式开启后内容精简，适配窄幅展示"
      },
      "strict_level": {
        "type": "integer",
        "default": 3,
        "description": "讲解严格度，越高则追问越深、要求越高"
      }
    },
    "required": ["exercise_content"]
  }
}
```

> 流式语音返回：WebSocket 音频流 + 同步文本流

##### 讲解提示词模板

```
讲解题目严格遵循交互式规则：
1. 分步引导式：每步只讲一个要点，结尾主动追问理解情况，禁止直接给答案。
2. 语言贴合学生认知水平，抽象概念用生活化类比。
3. 明确点明本题考察的知识点，关联用户掌握情况。
4. 严格等级越高，步骤拆分越细，追问越深入，对思路规范要求越高。
5. 卡片模式下每步内容控制在100字以内，用短句表达。
6. 实时语音输出时用短句、口语化表达，避免长难句和复杂符号，语速适中。
```

##### 单题卡片触发流程

1. 用户点击题目卡片右上角「AI 讲解」按钮，按钮进入加载状态
2. 前端自动携带题目参数，定向调用讲解工具，开启 `card_mode`
3. 加载完成后，卡片下方展开讲解区域，默认展示第一步内容
4. 每步底部内置「听懂了」「没听懂」反馈按钮，点击触发下一步或细化讲解
5. 全部步骤完成后，底部展示「在聊天中继续追问」按钮，点击跳转聊天抽屉
6. 讲解内容自动同步写入全局活跃会话，支持语音续问

#### 2.2 子模块 B：实时语音对话

##### 功能说明

聊天抽屉支持实时语音交互，按住说话、松开结束，边说边转写，AI 回复边生成边播放，实现自然对话体验。

##### 技术实现要点

- 前端：MediaRecorder 分片采集，WebSocket 流式上传，实时展示识别文本
- 后端：流式 ASR 识别 → 完整语句送入 Agent → 流式 TTS 合成 → 分片下发
- 支持打断：用户再次说话自动中断当前 TTS 播放，进入新一轮识别
- 支持切换：语音 / 文本模式无缝切换，对话历史统一展示

#### 2.3 子模块 C：交互式答疑与掌握度记录

##### 配套工具 `record_mastery_feedback`

```
{
  "name": "record_mastery_feedback",
  "description": "记录学生对知识点的掌握反馈，同步更新长期知识状态。",
  "parameters": {
    "type": "object",
    "properties": {
      "knowledge_point": {
        "type": "string",
        "description": "必填。知识点名称"
      },
      "feedback_level": {
        "type": "string",
        "enum": ["完全听懂", "部分听懂", "没听懂"],
        "description": "必填。学生反馈"
      },
      "question_id": {
        "type": "string"
      },
      "session_id": {
        "type": "string"
      }
    },
    "required": ["knowledge_point", "feedback_level"]
  }
}
```

------

### 模块 3：口语测评系统（独立大板块）

#### 3.1 模块总览

独立一级功能板块，分英语、语文两大方向，共 4 个子功能，全部支持实时语音交互，测评结果自动同步知识状态。

表格

| 学科 | 子功能         | 核心能力                                                     |
| ---- | -------------- | ------------------------------------------------------------ |
| 英语 | 考试听力题训练 | 同步教材 / 考试题型听力，播放音频 + 答题 + 批改 + 原文解析   |
| 英语 | 单词听写测评   | 按年级 / 单元选词，语音播报单词 + 拼写作答 + 批改 + 错词本生成 |
| 语文 | 古诗文默写提问 | 随机抽题语音提问 + 语音 / 文本作答 + 识别批改 + 易错点标注   |
| 语文 | 普通话水平测试 | 对标普通话考试，单字 / 词语 / 朗读 / 话题四维度评分 + 发音纠错 |

#### 3.2 子模块 A：英语考试听力训练

##### 工具 1：生成听力试卷 `generate_listening_test`

```
{
  "name": "generate_listening_test",
  "description": "生成指定题型、难度、数量的英语听力测试题，包含音频与题目选项。",
  "parameters": {
    "type": "object",
    "properties": {
      "question_type": {
        "type": "string",
        "enum": ["短对话", "长对话", "短文理解", "听写填空"],
        "description": "听力题型"
      },
      "difficulty": {
        "type": "string",
        "enum": ["简单", "中等", "困难"],
        "default": "中等"
      },
      "question_count": {
        "type": "integer",
        "default": 5
      },
      "grade": {
        "type": "string",
        "description": "对应年级"
      },
      "strict_level": {
        "type": "integer",
        "default": 3
      }
    },
    "required": ["question_type"]
  }
}
```

> 返回值：`{ "test_id": "xxx", "questions": [...], "audio_urls": [...] }`

##### 工具 2：提交听力答案并批改 `submit_listening_answers`

```
{
  "name": "submit_listening_answers",
  "description": "提交听力测试作答，自动批改评分，返回错题解析与知识点分析。",
  "parameters": {
    "type": "object",
    "properties": {
      "test_id": {
        "type": "string",
        "description": "必填。听力测试ID"
      },
      "answers": {
        "type": "array",
        "description": "用户答案列表"
      }
    },
    "required": ["test_id", "answers"]
  }
}
```

> 返回值：`{ "total_score": 0, "wrong_questions": [...], "analysis": "", "knowledge_points": [] }`

##### 业务流程

1. 用户在口语测评页选择「英语听力」，选择题型、难度、题量
2. 调用生成工具，返回题目与音频，前端逐题展示
3. 用户播放音频，选择答案，支持暂停、重播
4. 全部答完提交，自动批改，展示得分、错题解析、听力原文
5. 自动更新英语听力相关知识点掌握状态

#### 3.3 子模块 B：英语单词听写

##### 工具 1：生成听写任务 `generate_dictation_task`

```
{
  "name": "generate_dictation_task",
  "description": "生成指定范围的英语单词听写任务，按顺序播报单词发音。",
  "parameters": {
    "type": "object",
    "properties": {
      "word_scope": {
        "type": "string",
        "description": "单词范围，如「七年级上册Unit2」「高考高频词」"
      },
      "word_count": {
        "type": "integer",
        "default": 10
      },
      "play_speed": {
        "type": "string",
        "enum": ["慢速", "正常", "快速"],
        "default": "正常"
      },
      "strict_level": {
        "type": "integer",
        "default": 3
      }
    },
    "required": ["word_scope"]
  }
}
```

##### 工具 2：提交听写结果 `submit_dictation_result`

```
{
  "name": "submit_dictation_result",
  "description": "提交单词听写作答，自动批改，生成错词本。",
  "parameters": {
    "type": "object",
    "properties": {
      "task_id": {
        "type": "string"
      },
      "answers": {
        "type": "array",
        "description": "用户拼写的单词列表"
      }
    },
    "required": ["task_id", "answers"]
  }
}
```

##### 业务流程

1. 用户选择单词范围与数量，生成听写任务
2. 前端按顺序播报每个单词发音，每个单词播报 2 遍，间隔 3 秒
3. 用户输入拼写，支持上一个、下一个切换
4. 提交后自动批改，标红错误单词，显示正确拼写与释义
5. 支持生成本次错词本 PDF，错词自动同步至用户单词薄弱库

#### 3.4 子模块 C：语文古诗文默写提问

##### 工具 1：生成古诗文默写题 `generate_poetry_quiz`

```
{
  "name": "generate_poetry_quiz",
  "description": "从指定篇目中随机生成默写题，支持上下句填空、理解性默写。",
  "parameters": {
    "type": "object",
    "properties": {
      "poetry_scope": {
        "type": "string",
        "description": "篇目范围，如「七年级上册必背」「高中必背古诗文」"
      },
      "quiz_type": {
        "type": "string",
        "enum": ["上下句填空", "理解性默写"],
        "default": "上下句填空"
      },
      "question_count": {
        "type": "integer",
        "default": 5
      },
      "strict_level": {
        "type": "integer",
        "default": 3
      }
    },
    "required": ["poetry_scope"]
  }
}
```

##### 工具 2：提交默写作答 `submit_poetry_answer`

```
{
  "name": "submit_poetry_answer",
  "description": "提交古诗文默写作答，支持语音转写文本自动批改，标注易错字。",
  "parameters": {
    "type": "object",
    "properties": {
      "quiz_id": {
        "type": "string"
      },
      "user_answer": {
        "type": "string",
        "description": "用户作答文本，语音作答先经ASR转写"
      },
      "answer_mode": {
        "type": "string",
        "enum": ["text", "voice"]
      }
    },
    "required": ["quiz_id", "user_answer"]
  }
}
```

##### 业务流程

1. 用户选择篇目范围与题型，生成题目
2. AI 以语音形式读出题目（如「请默写《静夜思》的后两句」）
3. 用户可语音作答或文本输入，语音作答实时转写为文字
4. 提交后自动批改，标红错字、漏字，给出正确答案与易错提示
5. 错误诗句自动加入背诵薄弱清单，更新知识状态

#### 3.5 子模块 D：普通话口语测试

##### 工具定义 `evaluate_mandarin_test`

```
{
  "name": "evaluate_mandarin_test",
  "description": "对标普通话水平测试，从单字、词语、朗读、话题表达四个维度评分，给出发音纠错建议。",
  "parameters": {
    "type": "object",
    "properties": {
      "test_level": {
        "type": "string",
        "enum": ["二级甲等", "一级乙等", "一级甲等"],
        "default": "二级甲等",
        "description": "目标等级"
      },
      "test_part": {
        "type": "string",
        "enum": ["单字", "词语", "短文朗读", "命题说话"],
        "description": "测试分项，不传则完整测试"
      },
      "audio_file_id": {
        "type": "string",
        "description": "录音文件ID，实时测试为流式传入"
      },
      "strict_level": {
        "type": "integer",
        "default": 3
      }
    },
    "required": ["test_level"]
  }
}
```

> 返回值：`{ "total_score": 0, "dimension_scores": {"pronunciation": 0, "fluency": 0, "intonation": 0}, "error_words": [], "suggestions": "" }`

##### 业务流程

1. 用户选择目标等级与测试分项，开始测试
2. 系统展示题目内容（字表、词表、短文、话题），用户按住录音作答
3. 实时录音转写，测试完成后自动评分
4. 标注发音错误的字 / 词，给出正确发音示范与改进建议
5. 生成完整测评报告，支持导出 PDF

#### 3.6 口语测评专属提示词

```
你是专业的口语测评老师，严格按照对应标准进行评分与纠错。
【英语听力/听写】
- 评分重点：信息捕捉准确度、单词拼写正确率、连读弱读辨识度
- 错误标注精确到单词，给出发音要领与听力技巧
【古诗文默写】
- 严格对照原文，错字、漏字、多字均标注，易错字重点提醒
- 评分严格度越高，对字的书写细节、通假字、标点要求越高
【普通话测试】
- 四维度评分：语音标准度、词汇语法规范度、自然流畅度、内容完整度
- 平翘舌、前后鼻音、声调错误精确到字，给出针对性练习建议
所有测评完成后，自动调用 update_knowledge_state 更新对应能力维度。
```

------

### 模块 4：跨会话学生知识状态追踪

#### 4.1 功能说明

底层核心能力，全场景复用，动态维护用户知识点掌握画像，覆盖作业、讲解、口语、作文全学习行为；支持用户在聊天抽屉主动查询学情。

#### 4.2 核心写工具 `update_knowledge_state`

```
{
  "name": "update_knowledge_state",
  "parameters": {
    "type": "object",
    "properties": {
      "user_id": {
        "type": "string",
        "description": "必填。用户ID"
      },
      "knowledge_points": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "point_name": { "type": "string", "description": "知识点/能力维度名称" },
            "subject": { "type": "string", "description": "所属学科" },
            "mastery_change": {
              "type": "integer",
              "enum": [-2, -1, 0, 1, 2],
              "description": "掌握度变化"
            },
            "behavior_type": {
              "type": "string",
              "enum": ["作业正确", "作业错误", "听懂讲解", "订正正确", "练习正确", "练习错误", "口语正确", "口语错误", "作文提升点", "作文扣分点"],
              "description": "触发行为"
            }
          }
        }
      },
      "update_source": {
        "type": "string",
        "enum": ["作业分析", "题目讲解", "订正完成", "练习测试", "作文批改", "口语测评"],
        "description": "必填。更新来源"
      },
      "related_id": {
        "type": "string"
      }
    },
    "required": ["user_id", "knowledge_points", "update_source"]
  }
}
```

#### 4.3 核心读工具 `query_knowledge_state`

```
{
  "name": "query_knowledge_state",
  "description": "查询用户指定学科/时间范围的知识点掌握状态、薄弱点、学习建议。",
  "parameters": {
    "type": "object",
    "properties": {
      "subject": {
        "type": "string",
        "description": "可选，筛选学科，不传则全学科汇总"
      },
      "time_range": {
        "type": "string",
        "description": "可选，时间范围"
      },
      "query_type": {
        "type": "string",
        "enum": ["薄弱点查询", "掌握度汇总", "进步点分析", "学习建议"],
        "default": "掌握度汇总"
      }
    },
    "required": []
  }
}
```

#### 4.4 掌握等级规则

表格

| 等级     | 熟练度分数区间 | 教学策略                   |
| -------- | -------------- | -------------------------- |
| 未掌握   | 0-30 分        | 从基础概念讲起，搭配基础题 |
| 初步掌握 | 31-60 分       | 侧重方法应用，搭配中档题   |
| 熟练掌握 | 61-85 分       | 侧重综合应用，搭配变式题   |
| 精通     | 86-100 分      | 拓展拔高，搭配压轴题       |

#### 4.5 自动触发场景

- 作业分析报告生成完成
- 错题订正批改完成
- 学生反馈题目听懂 / 未听懂
- 听力 / 听写 / 默写 / 普通话测评完成
- 作文批改完成

------

### 模块 5：作文智能批改系统（独立大板块）

#### 5.1 模块总览

独立一级功能板块，覆盖语文、英语双学科，支持文本粘贴、图片上传、语音口述三种输入方式；提供总分、分项分、逐处修改建议、润色方案、参考范文，支持实时语音交互与 PDF 导出。

#### 5.2 核心批改工具 `correct_composition`

```
{
  "name": "correct_composition",
  "description": "对语文/英语作文进行智能批改、评分与润色，生成结构化批改报告，支持导出PDF。",
  "parameters": {
    "type": "object",
    "properties": {
      "composition_content": {
        "type": "string",
        "description": "作文文本内容（文本/语音转写模式传入）"
      },
      "composition_file_id": {
        "type": "string",
        "description": "作文图片/文档文件ID（文件模式传入）"
      },
      "subject": {
        "type": "string",
        "enum": ["语文", "英语"],
        "description": "必填。学科类型"
      },
      "grade": {
        "type": "string",
        "description": "年级"
      },
      "composition_title": {
        "type": "string",
        "description": "作文题目/主题"
      },
      "requirement": {
        "type": "string",
        "description": "作文要求/写作要求"
      },
      "strict_level": {
        "type": "integer",
        "default": 3,
        "description": "评分严格等级"
      },
      "generate_pdf": {
        "type": "boolean",
        "default": true
      }
    },
    "required": ["subject"]
  }
}
```

> 返回值结构：

```
{
  "correction_id": "xxx",
  "total_score": 42,
  "full_score": 60,
  "dimension_scores": {
    "立意": 12,
    "结构": 10,
    "内容": 11,
    "语言": 9
  },
  "overall_comment": "整体立意明确，结构完整，但语言表达较为平淡，缺少亮点修辞。",
  "revision_suggestions": [
    {
      "position": "第2段第3句",
      "original_text": "我很开心",
      "revised_text": "我的心里像揣了颗糖，甜丝丝的欢喜漫了开来",
      "reason": "表述过于平淡，改用感官描写更具画面感，提升文采",
      "revision_type": "语言润色"
    }
  ],
  "polish_advice": "建议增加细节描写，在叙事中加入环境与心理刻画，提升文章感染力。",
  "sample_essay": "参考范文全文...",
  "pdf_url": "xxx"
}
```

#### 5.3 专属批改系统提示词

```
你是资深的作文批改老师，严格按照对应学科的评分标准与当前严格等级进行批改。
【通用规则】
1. 评分符合对应年级考试评分标准，严格等级越高，打分越严，扣分点越多
2. 修改建议必须具体到字/词/句，给出「原文-修改后-修改理由」三段式结构
3. 修改类型分类：错别字、语病、用词不当、句式优化、文采提升、结构调整
4. 禁止笼统评价，所有建议必须可落地、可直接替换使用
5. 参考范文符合题目要求，水平略高于学生当前水平，具备学习借鉴价值
6. 批改完成后自动更新用户对应学科的写作能力知识状态

【语文作文批改维度（按满分60分比例）】
1. 立意（20%）：中心明确度、思想深度、扣题度
2. 结构（20%）：篇章结构、段落逻辑、过渡衔接
3. 内容（30%）：素材丰富度、论据充分性、情感真实性
4. 语言（30%）：表达流畅度、文采、字词标点错误
输出要求：总分、分项分、整体评价、逐处修改建议、总润色方向、参考范文

【英语作文批改维度（按满分25分比例）】
1. 内容（8分）：扣题度、要点完整性、逻辑连贯
2. 语言（12分）：语法准确性、词汇丰富度、句式多样性
3. 规范（5分）：拼写、标点、格式
输出要求：总分、分项分、错误标注、逐句润色建议、高分范文
```

#### 5.4 三种输入模式

1. **文本输入**：用户直接粘贴 / 输入作文全文，提交后立即批改
2. **图片上传**：用户上传手写作文图片，OCR 识别全文后进入批改
3. **语音口述**：用户实时语音口述作文内容，流式转写为文本，支持暂停、修改，确认后提交批改

#### 5.5 实时语音交互功能

- **语音问批改**：批改完成后，用户可语音提问「第三段怎么改更好」，AI 语音针对性解答
- **语音读范文**：一键语音朗读参考范文，支持调速、跟读
- **思路语音指导**：写作前可语音沟通写作思路，AI 实时给出提纲建议

#### 5.6 前端页面交互

- 左侧：作文编辑区（文本输入 / 图片上传 / 语音录入切换）
- 右侧：批改结果区（总分概览、分项得分、逐处修改标注、整体评价、参考范文）
- 修改建议原文标红，修改后标绿，悬浮显示修改理由
- 底部提供「导出 PDF 批改报告」「重新批改」「AI 语音讲解」按钮

#### 5.7 业务流程

1. 用户选择学科，输入作文内容（文本 / 图片 / 语音）
2. 文件 / 语音模式先做 OCR / ASR 预处理，得到纯文本
3. 调用批改工具，LLM 按评分标准完成结构化批改
4. 前端渲染批改结果，逐处标注修改建议
5. 生成 PDF 批改报告，上传 OSS
6. 自动更新用户写作知识状态

------

## 五、前端交互规范

### 5.1 全局导航布局

- 顶部导航栏：左侧 Logo，中间主导航（作业管理、口语测评、作文批改、学情分析），右侧用户头像入口
- 用户头像下拉：个人中心、助教设置、退出登录
- 全局右下角：悬浮 AI 助手按钮，点击展开聊天抽屉，全页面可用

### 5.2 悬浮聊天抽屉（ChatDrawer）交互

#### 5.2.1 基础布局

- 抽屉头部：当前模板名称标签（点击跳转设置）、会话标题、关闭按钮
- 主体区域：聊天消息流，支持文本、Markdown、PDF 卡片、语音控件、进度条
- 底部输入区：文本输入框、文件上传、语音输入按钮、快捷指令栏

#### 5.2.2 快捷指令预设

- 生成作业报告、生成错题本、查看学情、口语练习、作文批改
- 点击自动填充对应引导话术，支持自然语言补充参数

#### 5.2.3 实时语音交互

- 按住麦克风按钮说话，实时显示转写文字
- 松开自动发送，AI 回复以语音 + 文本双形式呈现
- 语音播放支持暂停、进度拖动，支持切换「仅语音」「仅文本」模式

### 5.3 助教设置页交互

- 顶部 4 套预设模板卡片，展示核心属性与适用人群，点击「一键应用」
- 下方自定义区域：性格类型单选、说话风格单选、语音音色下拉、严格程度滑块
- 实时预览：右侧展示当前配置下的示例回复
- 底部「保存配置」「恢复默认」按钮

### 5.4 题目卡片讲解交互

- 每张题目卡片右上角「AI 讲解」按钮
- 点击后卡片下方展开讲解区，分步展示，每步带「听懂了 / 没听懂」反馈
- 底部「去聊天中追问」按钮，跳转抽屉承接上下文

### 5.5 口语测评页交互

- 顶部 Tab 切换：英语听力、单词听写、古诗文默写、普通话测试
- 每个子功能左侧为设置区，右侧为答题区
- 录音按钮统一设计，按住录音、松开结束，实时显示波形
- 结果页分得分概览、错题详情、改进建议三部分

### 5.6 作文批改页交互

- 顶部学科切换：语文 / 英语
- 左右分栏：左编辑、右批改
- 修改建议 inline 标注，hover 显示详情
- 支持一键接受修改、一键复制范文

### 5.7 全局状态同步规则

1. 会话统一：所有功能入口共享全局活跃会话，交互记录互通
2. 配置统一：性格、严格度等配置一处修改，全场景实时生效
3. 学情统一：所有学习行为统一更新知识状态，跨模块数据打通

------

## 六、核心数据模型（MySQL）

### 6.1 用户表 `user`

表格

| 字段        | 类型     | 说明     |
| ----------- | -------- | -------- |
| user_id     | bigint   | 主键     |
| username    | varchar  | 用户名   |
| avatar      | varchar  | 头像     |
| create_time | datetime | 创建时间 |

### 6.2 会话表 `conversation`

表格

| 字段        | 类型     | 说明                |
| ----------- | -------- | ------------------- |
| session_id  | bigint   | 主键                |
| user_id     | bigint   | 用户 ID             |
| title       | varchar  | 会话标题            |
| subject     | varchar  | 关联学科            |
| status      | tinyint  | 状态：正常 / 已删除 |
| create_time | datetime | 创建时间            |
| update_time | datetime | 最后活跃时间        |

### 6.3 知识状态表 `user_knowledge_state`

表格

| 字段               | 类型     | 说明              |
| ------------------ | -------- | ----------------- |
| id                 | bigint   | 主键              |
| user_id            | bigint   | 用户 ID           |
| subject            | varchar  | 学科              |
| point_name         | varchar  | 知识点 / 能力维度 |
| mastery_score      | int      | 熟练度分数 0-100  |
| mastery_level      | varchar  | 掌握等级          |
| wrong_count        | int      | 累计错误次数      |
| last_practice_time | datetime | 最近练习时间      |
| update_time        | datetime | 更新时间          |

### 6.4 助教性格配置表 `agent_personality`

表格

| 字段             | 类型     | 说明            |
| ---------------- | -------- | --------------- |
| id               | bigint   | 主键            |
| user_id          | bigint   | 用户 ID（唯一） |
| template_name    | varchar  | 当前使用模板名  |
| personality_type | varchar  | 性格类型        |
| speaking_style   | varchar  | 说话风格        |
| voice_tone       | varchar  | 语音音色        |
| strict_level     | int      | 评分严格程度    |
| update_time      | datetime | 更新时间        |

### 6.5 作业分析报告表 `homework_report`

表格

| 字段         | 类型     | 说明          |
| ------------ | -------- | ------------- |
| report_id    | bigint   | 主键          |
| user_id      | bigint   | 用户 ID       |
| session_id   | bigint   | 会话 ID       |
| subject      | varchar  | 学科          |
| mode         | varchar  | 单作业 / 汇总 |
| correct_rate | float    | 正确率        |
| strict_level | int      | 批改严格度    |
| pdf_url      | varchar  | PDF 链接      |
| create_time  | datetime | 创建时间      |

### 6.6 错题表 `wrong_question`

表格

| 字段             | 类型     | 说明        |
| ---------------- | -------- | ----------- |
| id               | bigint   | 主键        |
| user_id          | bigint   | 用户 ID     |
| report_id        | bigint   | 关联报告 ID |
| subject          | varchar  | 学科        |
| knowledge_point  | varchar  | 知识点      |
| question_content | text     | 题干        |
| wrong_answer     | text     | 错误答案    |
| wrong_reason     | varchar  | 错因        |
| create_time      | datetime | 创建时间    |

### 6.7 口语测评相关表

#### 6.7.1 听力测试表 `listening_test`

表格

| 字段          | 类型     | 说明     |
| ------------- | -------- | -------- |
| test_id       | bigint   | 主键     |
| user_id       | bigint   | 用户 ID  |
| question_type | varchar  | 题型     |
| difficulty    | varchar  | 难度     |
| total_score   | int      | 总分     |
| user_score    | int      | 用户得分 |
| strict_level  | int      | 严格度   |
| create_time   | datetime | 创建时间 |

#### 6.7.2 单词听写任务表 `dictation_task`

表格

| 字段          | 类型     | 说明     |
| ------------- | -------- | -------- |
| task_id       | bigint   | 主键     |
| user_id       | bigint   | 用户 ID  |
| word_scope    | varchar  | 单词范围 |
| word_count    | int      | 单词数量 |
| correct_count | int      | 正确数量 |
| strict_level  | int      | 严格度   |
| create_time   | datetime | 创建时间 |

#### 6.7.3 古诗文默写表 `poetry_quiz_record`

表格

| 字段         | 类型     | 说明     |
| ------------ | -------- | -------- |
| id           | bigint   | 主键     |
| user_id      | bigint   | 用户 ID  |
| poetry_name  | varchar  | 篇目名称 |
| quiz_type    | varchar  | 题型     |
| is_correct   | tinyint  | 是否正确 |
| wrong_detail | varchar  | 错误细节 |
| create_time  | datetime | 创建时间 |

#### 6.7.4 普通话测评表 `mandarin_test_record`

表格

| 字段        | 类型     | 说明     |
| ----------- | -------- | -------- |
| id          | bigint   | 主键     |
| user_id     | bigint   | 用户 ID  |
| test_level  | varchar  | 目标等级 |
| total_score | float    | 总分     |
| test_part   | varchar  | 测试分项 |
| suggestions | text     | 改进建议 |
| audio_url   | varchar  | 录音链接 |
| create_time | datetime | 创建时间 |

### 6.8 作文批改表 `composition_correction`

表格

| 字段                 | 类型     | 说明         |
| -------------------- | -------- | ------------ |
| id                   | bigint   | 主键         |
| user_id              | bigint   | 用户 ID      |
| session_id           | bigint   | 会话 ID      |
| subject              | varchar  | 学科         |
| title                | varchar  | 作文题目     |
| total_score          | int      | 总分         |
| full_score           | int      | 满分         |
| content              | text     | 作文原文     |
| dimension_scores     | json     | 分项得分     |
| revision_suggestions | json     | 逐处修改建议 |
| overall_comment      | text     | 整体评价     |
| sample_essay         | text     | 参考范文     |
| strict_level         | int      | 批改严格度   |
| pdf_url              | varchar  | PDF 链接     |
| create_time          | datetime | 创建时间     |

### 6.9 题目讲解记录表 `question_explanation_record`

表格

| 字段                | 类型     | 说明           |
| ------------------- | -------- | -------------- |
| id                  | bigint   | 主键           |
| user_id             | bigint   | 用户 ID        |
| question_id         | bigint   | 关联题目 ID    |
| session_id          | bigint   | 关联会话 ID    |
| explanation_content | text     | 讲解步骤结构化 |
| feedback_level      | varchar  | 用户最终反馈   |
| create_time         | datetime | 创建时间       |

------

## 七、开发落地顺序（Vibe Coding 路线）

### Phase 1（基础底座与核心工具，3-5 天）

1. 搭建 FastAPI 项目骨架，集成 LangChain 与大模型
2. 实现会话管理 + 全局记忆 + 知识状态读写工具
3. 落地作业分析报告、错题订正本两大核心工具
4. 前端 ChatDrawer 基础聊天 + SSE 流式输出 + PDF 下载
5. 题目卡片 AI 讲解初版，文本分步讲解 + 反馈

### Phase 2（助教体系与实时语音，3-4 天）

1. 落地助教预设模板体系 + 性格配置工具 + 全局严格度控制
2. 实现流式 ASR/TTS 接入，聊天抽屉实时语音对话
3. 完善交互式答疑规则，讲解双端同步
4. 用户账号设置页开发，模板切换 + 自定义配置

### Phase 3（口语测评板块，4-5 天）

1. 落地英语听力、单词听写工具与页面
2. 落地古诗文默写、普通话测试工具与页面
3. 口语场景实时语音交互适配
4. 口语测评结果自动同步知识状态

### Phase 4（作文批改板块，3-4 天）

1. 落地语文 / 英语作文批改工具，结构化修改建议
2. 作文批改页面开发，逐处标注交互
3. 语音口述作文 + 语音讲解批改功能
4. PDF 批改报告导出

### Phase 5（全链路联调与优化，2-3 天）

1. 全入口状态同步：聊天、卡片、口语、作文、设置
2. 全场景知识状态自动更新验证
3. 异常处理、性能优化、体验打磨
4. 全流程测试与 bug 修复