from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "AI Tutor"
    DEBUG: bool = True

    # Dev mode: uses local storage + sync tasks (no Docker needed)
    DEV_MODE: bool = True

    # Database (MySQL only)
    # 注意：此处仅为未配置 .env 时的占位值，真实连接串必须放在 backend/.env 的
    # DATABASE_URL 中，严禁把真实密码写进本文件（会进入 git 历史）
    DATABASE_URL: str = "mysql+aiomysql://root:CHANGE_ME@localhost:3306/ai_tutor"

    # Redis (not needed in dev mode)
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO / S3 (not needed in dev mode, uses local storage instead)
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "ai-tutor"
    MINIO_SECURE: bool = False
    MINIO_PUBLIC_ENDPOINT: str = "localhost:9000"

    # Local storage (used when MinIO is unavailable or DEV_MODE=true)
    LOCAL_STORAGE_DIR: str = "./uploads"

    # JWT
    SECRET_KEY: str | None = None
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    @property
    def secret_key_valid(self) -> bool:
        """检查 SECRET_KEY 是否有效（非空、非默认值、长度 >= 16）"""
        if not self.SECRET_KEY:
            return False
        if self.SECRET_KEY in ("dev-secret-key-change-in-production", "secret", "change-me"):
            return False
        return len(self.SECRET_KEY) >= 16

    # LLM（默认走 DeepSeek：AI 助手对话、讲解、口语、知识点提取等全部文本类调用）
    LLM_API_KEY: str = ""
    LLM_API_BASE: str = "https://api.deepseek.com"
    LLM_MODEL: str = "deepseek-v4-flash"

    # ---- 视觉/多模态专用配置 ----
    # DeepSeek 不支持视觉输入，作业识别（AIGrader）、听写图片批改、
    # 作文图片批改等多模态任务继续使用 Qwen 系列模型。
    # 优先级高于 LLM_*，仅视觉调用点读取该组配置。
    VISION_API_KEY: str = ""
    # 默认指向阿里云百炼公共网关（非账号专属地址）；个人专属 MaaS 实例地址
    # 请配置在 .env 的 VISION_API_BASE，不要把账号专属 URL 写进代码
    VISION_API_BASE: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    VISION_MODEL: str = "qwen3.7-plus"

    # 首启引导：users 表为空时自动创建初始管理员（注册功能已移除，防止空库部署无法开户）。
    # 密码留空时 dev 模式随机生成并打印到控制台；生产模式必须显式配置，否则启动失败。
    FIRST_ADMIN_PHONE: str = "test"
    FIRST_ADMIN_PASSWORD: str = ""
    # 单设备登录开关：true=新登录踢掉旧设备（token_version 校验）；false=允许同一账号多设备共存
    SINGLE_DEVICE_LOGIN: bool = True

    # ---- Agent 可靠性配置 ----
    # 单次非流式 LLM 调用超时（秒）。ReAct 每轮决策调用走这个值，
    # 超时后降级为不带工具直接回答，避免单轮挂起拖垮整轮对话。
    LLM_REQUEST_TIMEOUT: int = 60
    # 最终回答流式调用超时（秒）
    LLM_STREAM_TIMEOUT: int = 60
    # Agent 整体时间预算（秒）：路由命中后的 ReAct 循环总时长上限。
    # 240 的取值权衡：报告生成工具自身约 100~150s（含嵌套 LLM + playwright 渲染），
    # 预算太小会让报告生成被强制中断；240 可覆盖一次完整报告 + 兜底回答。
    AGENT_TIME_BUDGET: int = 240
    # 最终回答最低预留时间（秒）：预算剩余不足该值时强制停止调工具、直接出文
    AGENT_MIN_FINAL_BUDGET: int = 30
    # 默认单工具执行超时（秒），生成类工具在 tools.py 的 TOOL_TIMEOUTS 中单独覆盖
    TOOL_EXEC_TIMEOUT: int = 60
    # 工具结果塞回 LLM messages 前的最大字符数（控制上下文膨胀，防止越聊越慢）
    TOOL_RESULT_MAX_CHARS: int = 6000
    
    # AI 评分器配置
    GRADER_MAX_OUTPUT_TOKENS: int = 8000
    GRADER_MAX_IMAGES_PER_REQUEST: int = 2
    GRADER_MAX_RETRIES: int = 2
    
    # Agent 执行器配置
    AGENT_MAX_OUTPUT_TOKENS: int = 4096
    AGENT_MAX_ITERATIONS: int = 5

    # 是否信任反向代理的 X-Forwarded-For 头（用于登录限流等获取真实 IP）。
    # false（默认）：直接用 request.client.host，不信任客户端可伪造的 XFF 头
    #（防止攻击者旋转 XFF 绕过限流、或用他人 IP 填 XFF 借刀锁定）；
    # true：部署在可信反向代理后时开启，取 XFF 最右侧一段视为真实客户端。
    TRUST_PROXY_HEADERS: bool = False

    # 是否启用关键词路由层（DEV 调试时可关闭走回全量 schema 老逻辑）
    ROUTE_ENABLED: bool = True
    # 关键词路由未命中时，是否用 LLM 意图分类兜底：
    # true=判定需要查库时带 4 个查询工具走 ReAct；false=维持纯聊天快路径
    ROUTER_CLASSIFY_ENABLED: bool = True
    # 意图分类专用模型；留空则复用 LLM_MODEL（成本敏感时可用更小更快的模型，如 deepseek-chat）
    ROUTER_CLASSIFY_MODEL: str = ""
    # 意图分类调用超时（秒）。分类超时/失败/解析失败一律回落纯聊天，最坏结果=直接回答
    ROUTER_CLASSIFY_TIMEOUT: int = 10
    # 意图分类时携带的最近历史对话轮数（识别"那英语的呢"等跨轮追问）；0 表示不带历史
    ROUTER_CLASSIFY_HISTORY_TURNS: int = 4

    # 讯飞语音评测（流式版 ISE，普通话测评用）
    XFYUN_APP_ID: str = ""
    XFYUN_API_KEY: str = ""
    XFYUN_API_SECRET: str = ""
    XFYUN_ISE_URL: str = "wss://ise-api.xfyun.cn/v2/open-ise"

    # CORS（逗号分隔的域名列表；DEV_MODE 未配置时默认 localhost）
    CORS_ORIGINS: str = ""

    # File Upload
    MAX_UPLOAD_SIZE_MB: int = 50

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
