import os
import secrets
from contextlib import asynccontextmanager
from typing import List
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1 import auth, assignments, questions, analytics, error_questions, ai_tutor, ai_questions, conversations, personality, compositions, oral_assessments, usage_stats, users, favorites, upload_questions
from app.core.config import get_settings
from app.core.deps import get_db
from app.db.session import engine, async_session_factory
from app.db.base import Base

settings = get_settings()


def _ensure_secret_key():
    """Ensure SECRET_KEY is set. Auto-generate in dev mode, refuse startup in production."""
    if settings.secret_key_valid:
        return
    if settings.DEV_MODE:
        generated = secrets.token_urlsafe(32)
        settings.SECRET_KEY = generated
        print("[startup] DEV_MODE: auto-generated SECRET_KEY", flush=True)
    else:
        raise RuntimeError(
            "SECRET_KEY is not configured. Set it in .env or environment variable. "
            "Do NOT use the default dev key in production."
        )


def _validate_production_config():
    """生产环境关键配置校验"""
    if settings.DEV_MODE:
        return
    
    errors = []
    if not settings.VISION_API_KEY:
        errors.append("VISION_API_KEY: 作业评分依赖视觉大模型，必须配置")
    if not settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_PUBLIC_ENDPOINT == "localhost:9000":
        errors.append("MINIO_PUBLIC_ENDPOINT: 生产环境必须配置公网可访问的 MinIO 地址")
    if not settings.LLM_API_KEY:
        errors.append("LLM_API_KEY: AI 对话功能依赖 LLM，必须配置")
    
    if errors:
        raise RuntimeError(
            "生产环境配置缺失，请在 .env 中设置以下项：\n" + "\n".join(f"  - {e}" for e in errors)
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: validate critical config
    _ensure_secret_key()
    _validate_production_config()

    # 安装 LLM Token 用量全局追踪（数据看板数据源）
    from app.services.llm_usage_tracker import install_llm_usage_tracker
    install_llm_usage_tracker()

    # Create tables (no-op for tables that already exist in MySQL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Auto-migrate: add new columns that may not exist yet
        await _auto_migrate(conn)

    # 开户引导：空库时创建初始管理员 + 幂等提升 test 账号（注册已取消，此为开户入口）
    await _ensure_bootstrap_admin()

    # LLM 用量日志保留策略：启动时清理 90 天前的旧记录，防止表无限膨胀
    from app.services.llm_usage_tracker import cleanup_old_usage_logs
    await cleanup_old_usage_logs(retention_days=90)

    # dev 模式启动自愈：收敛服务重启后残留的"分析中"作业（详见 analysis_tasks.reconcile_stuck_assignment），
    # 避免作业永久卡在 grading。生产模式由 Celery worker 自身保证，不做启动扫描。
    if settings.DEV_MODE:
        from app.tasks.analysis_tasks import reconcile_all_stuck_assignments
        await reconcile_all_stuck_assignments()

    yield
    # 服务关闭时 flush 用量追踪缓冲区（比 atexit 更可靠）
    from app.services.llm_usage_tracker import flush_on_shutdown
    await flush_on_shutdown()
    await engine.dispose()


async def _ensure_bootstrap_admin():
    """开户引导 + test 账号提升，两件事一起做（幂等，重复启动无副作用）。

    1. 引导创建：注册功能已移除（开户走 POST /users，需 admin 身份），空库部署会出现
       '无账号可登录'死锁，因此 users 表为空时自动创建初始超级管理员：
       - 手机号：settings.FIRST_ADMIN_PHONE（默认 test）
       - 密码：settings.FIRST_ADMIN_PASSWORD；留空时 dev 模式随机生成并打印到控制台，
         生产模式则启动失败（杜绝无声的默认密码）
    2. 幂等提升：已有 phone='test' 的账号提升为超级管理员（历史部署迁移路径保留）

    注意：此处自建 session，必须显式 commit（不走 get_db 依赖的自动提交）。
    """
    from sqlalchemy import func, select
    from app.core.security import hash_password
    from app.models.user import User, UserRole

    async with async_session_factory() as session:
        count = (await session.execute(select(func.count(User.id)))).scalar() or 0
        if count == 0:
            # ── 引导创建初始管理员 ──
            phone = (settings.FIRST_ADMIN_PHONE or "test").strip()
            password = settings.FIRST_ADMIN_PASSWORD
            if not password:
                if not settings.DEV_MODE:
                    raise RuntimeError(
                        "users 表为空且未配置 FIRST_ADMIN_PASSWORD："
                        "全新生产部署必须显式设置初始管理员密码（.env 中 FIRST_ADMIN_PHONE / FIRST_ADMIN_PASSWORD）"
                    )
                password = secrets.token_urlsafe(12)
            session.add(User(
                phone=phone,
                username="管理员",
                hashed_password=hash_password(password),
                role=UserRole.ADMIN,
            ))
            await session.commit()
            # 使用 logger 而非 print，避免密码泄露到日志采集系统（如 ELK）
            import logging as _logging
            _startup_logger = _logging.getLogger("app.startup")
            if settings.FIRST_ADMIN_PASSWORD:
                _startup_logger.warning(
                    "[startup] 已创建初始管理员账号：%s / 密码：已配置（来自 FIRST_ADMIN_PASSWORD 配置，不记录明文）",
                    phone,
                )
            else:
                _startup_logger.warning(
                    "[startup] 已创建初始管理员账号：%s，密码已随机生成。请尽快登录并修改密码。",
                    phone,
                )
                # 仅在交互式终端输出密码（不在日志中持久化）
                import sys
                if sys.stdout.isatty():
                    print(f"[startup] 初始管理员密码：{password}（仅本次显示，请记录并修改）", flush=True)
            return

        # ── 幂等提升已有 test 账号 ──
        result = await session.execute(select(User).where(User.phone == "test"))
        user = result.scalar_one_or_none()
        if user is None:
            print("[startup] test 账号不存在，跳过超级管理员提升", flush=True)
            return
        if user.role != UserRole.ADMIN:
            user.role = UserRole.ADMIN
            await session.commit()
            print("[startup] 已将 test 账号提升为超级管理员", flush=True)
        else:
            print("[startup] test 账号已是超级管理员，无需处理", flush=True)


async def _auto_migrate(conn):
    """Add missing columns to existing tables (safe, idempotent)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    migrations = [
        # assignment_questions: question_type (added 2024-06)
        "ALTER TABLE assignment_questions ADD COLUMN question_type VARCHAR(64) NULL",
        # assignment_questions: common_mistakes (added 2026-06)
        "ALTER TABLE assignment_questions ADD COLUMN common_mistakes JSON NULL",
        # 大题套小题：父子层级 (added 2026-06)
        "ALTER TABLE assignment_questions ADD COLUMN parent_id INT NULL",
        "ALTER TABLE assignment_questions ADD COLUMN sub_question_index INT NULL",
        "ALTER TABLE assignment_questions ADD INDEX ix_question_parent_id (parent_id)",
        "ALTER TABLE assignment_questions ADD CONSTRAINT fk_question_parent FOREIGN KEY (parent_id) REFERENCES assignment_questions(id) ON DELETE CASCADE",
        # 答案图片与人工审核备注 (added 2026-06)
        "ALTER TABLE assignment_questions ADD COLUMN answer_image_url VARCHAR(512) NULL",
        "ALTER TABLE assignment_questions ADD COLUMN manual_review_note TEXT NULL",
        # ai_generated_questions: 大题分组字段 (added 2026-06)
        "ALTER TABLE ai_generated_questions ADD COLUMN group_id VARCHAR(36) NULL",
        "ALTER TABLE ai_generated_questions ADD COLUMN sub_question_index INT NULL",
        "ALTER TABLE ai_generated_questions ADD COLUMN question_context TEXT NULL",
        "ALTER TABLE ai_generated_questions ADD INDEX ix_ai_q_group_id (group_id)",
        # ai_generated_questions: 完整解析字段 (added 2026-07)
        "ALTER TABLE ai_generated_questions ADD COLUMN analysis TEXT NULL",
        # ai_generated_questions: 题目配图 SVG 字段 (added 2026-08)
        "ALTER TABLE ai_generated_questions ADD COLUMN image_svg TEXT NULL COMMENT '题目配图SVG代码'",
        "ALTER TABLE ai_generated_questions ADD COLUMN context_image_svg TEXT NULL COMMENT '大题背景材料配图SVG代码'",
        # ai_generated_questions: 上传题自有元数据（年级/科目/学期，added 2026-08）
        "ALTER TABLE ai_generated_questions ADD COLUMN grade VARCHAR(32) NULL COMMENT '年级（上传题自有元数据）'",
        "ALTER TABLE ai_generated_questions ADD COLUMN subject VARCHAR(32) NULL COMMENT '科目（上传题自有元数据）'",
        "ALTER TABLE ai_generated_questions ADD COLUMN semester VARCHAR(32) NULL COMMENT '学期（上传题自有元数据）'",
        # ai_generated_questions: 题目来源列（upload=自有试题，added 2026-08）
        # 上传转录与 AI 生成共用本表，靠此列区分（收藏页"题目来源"筛选依据）
        "ALTER TABLE ai_generated_questions ADD COLUMN source VARCHAR(16) NULL COMMENT '题目来源：upload=自有试题(上传转录), NULL/ai=AI生成'",
        # ai_generated_questions: 原题图像列（上传转录的自有试题，added 2026-08）
        # 存图片原文件或扫描 PDF 渲染首页的存储标识（经预签名返回可访问 URL），
        # 供收藏页编辑弹窗左栏对照原图；老数据为 NULL 时回落 SVG 配图展示
        "ALTER TABLE ai_generated_questions ADD COLUMN image_url TEXT NULL COMMENT '原题图像存储标识（上传转录）'",
        # 历史回填：source 列上线前上传的题目 grade 三列恒齐全（AI 生成题恒为 NULL），
        # 以此区分回填；COALESCE 只填 NULL，每次启动可安全重跑（幂等）
        "UPDATE ai_generated_questions SET source='upload' WHERE source IS NULL AND grade IS NOT NULL AND subject IS NOT NULL AND semester IS NOT NULL",
        # 会话管理 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS conversations ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  title VARCHAR(128) NOT NULL DEFAULT '新对话',"
        "  subject VARCHAR(32) NULL,"
        "  status SMALLINT NOT NULL DEFAULT 1,"
        "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "  INDEX ix_conversations_user_id (user_id),"
        "  CONSTRAINT fk_conversations_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        "CREATE TABLE IF NOT EXISTS conversation_messages ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  conversation_id INT NOT NULL,"
        "  role VARCHAR(16) NOT NULL,"
        "  content TEXT NOT NULL,"
        "  reasoning TEXT NULL,"
        "  tool_calls JSON NULL,"
        "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_conversation_messages_conversation_id (conversation_id),"
        "  CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE"
        ")",
        # 助教性格配置 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS agent_personality ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL UNIQUE,"
        "  template_name VARCHAR(32) NOT NULL DEFAULT '严谨专业型',"
        "  personality_type VARCHAR(32) NOT NULL DEFAULT '严谨专业型',"
        "  speaking_style VARCHAR(32) NOT NULL DEFAULT '书面化正式',"
        "  voice_tone VARCHAR(32) NOT NULL DEFAULT '沉稳男声',"
        "  strict_level INT NOT NULL DEFAULT 3,"
        "  update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
        "  CONSTRAINT fk_personality_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 作文批改 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS composition_corrections ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  session_id INT NULL,"
        "  subject VARCHAR(16) NOT NULL,"
        "  title VARCHAR(255) NOT NULL DEFAULT '未命名作文',"
        "  total_score INT NOT NULL DEFAULT 0,"
        "  full_score INT NOT NULL DEFAULT 60,"
        "  content TEXT NOT NULL,"
        "  requirement TEXT NULL,"
        "  grade VARCHAR(32) NULL,"
        "  dimension_scores JSON NULL,"
        "  revision_suggestions JSON NULL,"
        "  overall_comment TEXT NULL,"
        "  polish_advice TEXT NULL,"
        "  sample_essay TEXT NULL,"
        "  strict_level INT NOT NULL DEFAULT 3,"
        "  pdf_url VARCHAR(512) NULL,"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_composition_user_id (user_id),"
        "  CONSTRAINT fk_composition_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 作文批改 - essay_type 列 (added 2026-07)
        "ALTER TABLE composition_corrections ADD COLUMN essay_type VARCHAR(32) NULL COMMENT '作文类型：读后续写/应用文/议论文等'",
        # 口语测评 - 听力测试 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS listening_tests ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  question_type VARCHAR(32) NOT NULL,"
        "  difficulty VARCHAR(16) DEFAULT '中等',"
        "  question_count INT DEFAULT 5,"
        "  total_score FLOAT DEFAULT 0,"
        "  user_score FLOAT DEFAULT 0,"
        "  strict_level INT DEFAULT 3,"
        "  grade VARCHAR(32) NULL,"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_listening_user_id (user_id),"
        "  CONSTRAINT fk_listening_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        "CREATE TABLE IF NOT EXISTS dictation_tasks ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  word_scope VARCHAR(128) NOT NULL,"
        "  word_count INT DEFAULT 10,"
        "  correct_count INT DEFAULT 0,"
        "  strict_level INT DEFAULT 3,"
        "  play_speed VARCHAR(16) DEFAULT '正常',"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_dictation_user_id (user_id),"
        "  CONSTRAINT fk_dictation_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        "CREATE TABLE IF NOT EXISTS mandarin_test_records ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  test_level VARCHAR(16) NOT NULL,"
        "  test_part VARCHAR(32) NULL,"
        "  total_score FLOAT DEFAULT 0,"
        "  dimension_scores TEXT NULL,"
        "  suggestions TEXT NULL,"
        "  audio_url VARCHAR(512) NULL,"
        "  strict_level INT DEFAULT 3,"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_mandarin_user_id (user_id),"
        "  CONSTRAINT fk_mandarin_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 口语测评作业记录（统一表）(added 2026-07)
        "CREATE TABLE IF NOT EXISTS oral_records ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  category VARCHAR(32) NOT NULL,"
        "  name VARCHAR(128) NOT NULL,"
        "  score VARCHAR(64) NULL,"
        "  grade_level VARCHAR(16) NULL COMMENT '学段：小学/初中/高中',"
        "  detail TEXT NULL,"
        "  create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_oral_records_user_id (user_id),"
        "  INDEX ix_oral_records_category (category),"
        "  CONSTRAINT fk_oral_records_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 口语测评作业记录 - 补充 grade_level 列 (added 2026-07-22)
        # 注意：此 ALTER 必须在 CREATE TABLE 之后，否则表不存在时 ALTER 会失败
        "ALTER TABLE oral_records ADD COLUMN grade_level VARCHAR(16) NULL COMMENT '学段：小学/初中/高中'",
        # 口语测评作业记录 - 高频筛选冗余列 (added 2026-08)
        # 与 OralRecord 模型保持一致：从 detail JSON 冗余到独立列，支持 SQL 层筛选。
        # 若缺失，list_oral_records 查询会报 Unknown column 导致接口 500（记录列表不显示）
        "ALTER TABLE oral_records ADD COLUMN detail_question_type VARCHAR(32) NULL COMMENT '题型（冗余自detail）'",
        "ALTER TABLE oral_records ADD COLUMN detail_word_scope VARCHAR(128) NULL COMMENT '词库范围（冗余自detail）'",
        "ALTER TABLE oral_records ADD COLUMN detail_direction VARCHAR(32) NULL COMMENT '测试方向（冗余自detail）'",
        "ALTER TABLE oral_records ADD COLUMN detail_difficulty VARCHAR(16) NULL COMMENT '难度（冗余自detail）'",
        "ALTER TABLE oral_records ADD INDEX ix_oral_records_detail_question_type (detail_question_type)",
        "ALTER TABLE oral_records ADD INDEX ix_oral_records_detail_difficulty (detail_difficulty)",
        # 口语测评作业记录 - 冗余筛选列历史数据回填 (added 2026-08)
        # 冗余列上线前创建的旧记录冗余列为 NULL（但 detail JSON 中一直存有题型/难度等字段），
        # 导致记录列表卡片无标签、SQL 层筛选（题型/难度下拉）匹配不到旧记录。
        # JSON_VALID 守卫避免非 JSON detail 导致整条 UPDATE 失败（报 3141 不在安全码白名单）；
        # COALESCE 保证幂等（只填 NULL，每次启动可安全重跑）。
        "UPDATE oral_records SET"
        " detail_question_type = COALESCE(detail_question_type, JSON_UNQUOTE(JSON_EXTRACT(detail, '$.question_type'))),"
        " detail_word_scope = COALESCE(detail_word_scope, JSON_UNQUOTE(JSON_EXTRACT(detail, '$.word_scope'))),"
        " detail_direction = COALESCE(detail_direction, JSON_UNQUOTE(JSON_EXTRACT(detail, '$.direction'))),"
        " detail_difficulty = COALESCE(detail_difficulty, JSON_UNQUOTE(JSON_EXTRACT(detail, '$.difficulty'))),"
        " grade_level = COALESCE(NULLIF(grade_level, ''), NULLIF(JSON_UNQUOTE(JSON_EXTRACT(detail, '$.grade_level')), ''))"
        " WHERE detail IS NOT NULL AND JSON_VALID(detail)"
        " AND (detail_question_type IS NULL OR detail_word_scope IS NULL"
        "  OR detail_direction IS NULL OR detail_difficulty IS NULL)",
        # 知识状态追踪 (added 2026-07)
        "CREATE TABLE IF NOT EXISTS user_knowledge_state ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  subject VARCHAR(32) NOT NULL DEFAULT '通用',"
        "  point_name VARCHAR(128) NOT NULL,"
        "  mastery_score INT NOT NULL DEFAULT 50,"
        "  mastery_level VARCHAR(16) NOT NULL DEFAULT '初步掌握',"
        "  wrong_count INT NOT NULL DEFAULT 0,"
        "  correct_count INT NOT NULL DEFAULT 0,"
        "  last_practice_time DATETIME NULL,"
        "  update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  INDEX ix_knowledge_state_user_id (user_id),"
        "  INDEX ix_knowledge_state_subject (subject),"
        "  CONSTRAINT fk_knowledge_state_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 月份字段更名 month → usage_month (added 2026-07-22)
        "ALTER TABLE assignments CHANGE COLUMN month usage_month VARCHAR(16) NOT NULL",
        # 月份字段二次更名 applicable_month → usage_month
        "ALTER TABLE assignments CHANGE COLUMN applicable_month usage_month VARCHAR(16) NOT NULL",
        # 客观题识别区（答题卡）切图 (added 2026-07-29)
        "ALTER TABLE assignments ADD COLUMN answer_sheet_image_url VARCHAR(512) NULL",
        # 角色体系调整：教师(teacher) 统一改为普通用户(user)，删除教师角色 (added 2026-08-04)
        # 历史库 role 列为 ENUM('TEACHER','ADMIN')（大写），直接 UPDATE 写入 'USER' 会触发
        # MySQL 严格模式 1265 报错，因此必须三步走（顺序不可颠倒）：
        #   1. 先扩充枚举（保留旧值）→ 2. 转换存量数据 → 3. 再收紧为最终枚举
        # 注意1：MySQL ENUM 值大小写不敏感，枚举列表里不能同时出现大小写变体（否则报
        #        1291 duplicated value），因此全部只用大写成员名。
        # 注意2：最终值必须用枚举【成员名】大写 'USER'/'ADMIN' —— SQLAlchemy 的 SAEnum
        #        默认按成员名映射（_object_lookup 键为大写），存小写 'user' 会导致 ORM
        #        读回时 LookupError 序列化失败（表现为用户列表接口 500）。
        # 重复执行幂等：步骤 1 在已收紧的列上仍可成功扩充，步骤 2 无匹配行，步骤 3 收回收紧
        "ALTER TABLE users MODIFY COLUMN role ENUM('USER','ADMIN','TEACHER') NOT NULL DEFAULT 'USER'",
        "UPDATE users SET role='USER' WHERE role IN ('TEACHER','teacher')",
        "ALTER TABLE users MODIFY COLUMN role ENUM('USER','ADMIN') NOT NULL DEFAULT 'USER'",
        # 角色降级审计：TEACHER 历史账号被统一降级为普通用户，这里记录受影响行数。
        # 注意：这条 SQL 在 roles 收紧之后执行，若已收敛过（幂等）则无匹配行，结果恒为 0 属正常。
        # 单设备登录：token 版本号 (added 2026-08)
        "ALTER TABLE users ADD COLUMN token_version INT NOT NULL DEFAULT 0",
        # 状态机收敛：PROCESSING 统一改为 GRADING（PROCESSING 仅保留用于读兼容，不再写入）
        # 幂等：已收敛或无 PROCESSING 数据时无匹配行，结果恒为 0 属正常
        "UPDATE assignments SET status='grading' WHERE status='processing'",
        # 用户模型重构：手机号与用户名分离 (added 2026-08)
        # 原 username 字段直接存储手机号，现拆分为 phone（登录账号）+ username（显示名称）
        # 步骤1：添加 phone 列（先 nullable，待填充数据后再收紧）
        "ALTER TABLE users ADD COLUMN phone VARCHAR(20) NULL",
        # 步骤2：将现有 username 中的手机号数据复制到 phone
        "UPDATE users SET phone = username WHERE phone IS NULL",
        # 步骤3：phone 收紧为 NOT NULL
        "ALTER TABLE users MODIFY COLUMN phone VARCHAR(20) NOT NULL",
        # 步骤4：phone 添加唯一索引
        "ALTER TABLE users ADD UNIQUE INDEX ix_users_phone (phone)",
        # 步骤5：username 改为 nullable（显示名称，不再用作登录账号）
        "ALTER TABLE users MODIFY COLUMN username VARCHAR(64) NULL",
        # 步骤6：清除 username 中的旧手机号数据（已复制到 phone），设为 NULL
        # 关键：只清除 username 本身就是手机号格式的记录（^\\d{11}$），
        # 保留用户自定义的非手机号显示名（如"小明妈妈"），避免数据丢失。
        "UPDATE users SET username = NULL WHERE username = phone AND phone REGEXP '^[0-9]{11}$'",
        # 步骤7：删除 username 上的旧唯一索引（MySQL error 1091 = Can't DROP, 安全忽略）
        # 注意索引名是 ix_users_username（SQLAlchemy 自动命名）——旧版写成
        # DROP INDEX username 会报 1091 被静默忽略，唯一约束从未删除，
        # username 恢复为可空后仍残留唯一性约束（同名显示名无法入库）。
        # 若库中恰好存在名为 username 的旧索引（更老版本），1091/1092 都会安全忽略。
        "ALTER TABLE users DROP INDEX ix_users_username",
        # 题目识别：新增题干文本字段（含 $...$ 包裹的 LaTeX 公式），
        # 老数据该列为 NULL，前端判空回落图片展示，无需数据迁移
        "ALTER TABLE assignment_questions ADD COLUMN question_text TEXT NULL",
        # 我的收藏 (added 2026-08)
        # question_id 同时指向 assignment_questions / ai_generated_questions 两张表，
        # MySQL 不支持跨表条件外键，故不建 FK，存在性由应用层校验
        "CREATE TABLE IF NOT EXISTS user_favorites ("
        "  id INT AUTO_INCREMENT PRIMARY KEY,"
        "  user_id INT NOT NULL,"
        "  item_type VARCHAR(16) NOT NULL,"
        "  question_id INT NOT NULL,"
        "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
        "  UNIQUE KEY uq_user_favorites (user_id, item_type, question_id),"
        "  INDEX ix_user_favorites_user_id (user_id),"
        "  CONSTRAINT fk_user_favorites_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
        ")",
        # 作文批改异步化：批改状态机字段 (added 2026-08)
        # 存量记录默认 completed（已有分数直接可用），新建记录在 API 层显式写 pending
        "ALTER TABLE composition_corrections ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'completed' COMMENT '状态：pending/correcting/completed/failed'",
        "ALTER TABLE composition_corrections ADD COLUMN error_message TEXT NULL COMMENT '批改失败原因'",
        # 作文批改 - 字数统计列 (added 2026-08)
        # 与模型 CompositionCorrection.word_count 保持一致；缺失时 ORM 查询
        # SELECT 全列会报 Unknown column 导致历史列表/详情接口 500（记录不显示）
        "ALTER TABLE composition_corrections ADD COLUMN word_count INT NOT NULL DEFAULT 0 COMMENT '作文字数（不含标点）'",
        "ALTER TABLE composition_corrections ADD COLUMN deductions JSON NULL COMMENT '扣分明细：键为扣分原因，值为扣分分值'",
    ]
    # 安全忽略的 MySQL 错误码（幂等迁移中预期会遇到的"已存在/不存在"情况）
    # 1050 表已存在 / 1054 未知列 / 1060 列已存在 / 1061 索引已存在 /
    # 1091 无法 DROP 列 / 1092 无法 DROP 索引 / 1146 表不存在 /
    # 1826 重复的外键约束名（如重复建 fk_question_parent）/
    # 1138 无效使用 NULL（如 NOT NULL 列 UPDATE 置 NULL，出现在"先收紧再清数据"的幂等场景）
    _SAFE_ERR_CODES = (1050, 1054, 1060, 1061, 1091, 1092, 1146, 1826, 1138)

    # ── 特殊迁移：ai_generated_questions.source_question_id 外键改为 ON DELETE SET NULL (added 2026-08)
    # 旧库该外键为默认 RESTRICT 规则（约束名 ai_generated_questions_ibfk_N，由 MySQL 按创建顺序
    # 自动命名，不能硬编码），删除作业时若题目被 AI 题/转录题引用，外键直接拒绝删除 → 接口 500。
    # 必须先查 information_schema 拿到实际约束名，确认删除规则不是 SET NULL 后再 DROP + 重建。
    # 幂等：新库（create_all 已建 SET NULL）/已迁移过的库查询 DELETE_RULE 为 SET NULL 直接跳过。
    try:
        _fk_row = (await conn.execute(text(
            "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_generated_questions' "
            "AND COLUMN_NAME = 'source_question_id' AND REFERENCED_TABLE_NAME = 'assignment_questions'"
        ))).first()
        if _fk_row:
            _fk_name = _fk_row[0]
            _rule_row = (await conn.execute(text(
                "SELECT DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
                "WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = :fk_name"
            ), {"fk_name": _fk_name})).first()
            if _rule_row and _rule_row[0] != "SET NULL":
                await conn.execute(text(
                    f"ALTER TABLE ai_generated_questions DROP FOREIGN KEY `{_fk_name}`"
                ))
                await conn.execute(text(
                    "ALTER TABLE ai_generated_questions ADD CONSTRAINT fk_ai_q_source_question "
                    "FOREIGN KEY (source_question_id) REFERENCES assignment_questions(id) ON DELETE SET NULL"
                ))
                print(f"[migrate] OK: 外键 {_fk_name} → ON DELETE SET NULL", flush=True)
    except OperationalError as e:
        # 与上方循环一致的容错：白名单错误码跳过，非白名单按模式决定告警或启动失败
        _err_code = 0
        if hasattr(e, 'orig') and e.orig and hasattr(e.orig, 'args'):
            _err_code = e.orig.args[0] if e.orig.args else 0
        if _err_code not in _SAFE_ERR_CODES:
            _msg = f"[migrate] WARN (errno {_err_code}): FK 迁移失败 -> {e}"
            print(_msg, flush=True)
            if not settings.DEV_MODE:
                raise RuntimeError(f"数据库迁移失败，应用拒绝在未完全迁移的 schema 上启动：{_msg}")

    for sql in migrations:
        try:
            result = await conn.execute(text(sql))
            print(f"[migrate] OK: {sql[:60]}...", flush=True)
            # 角色降级审计：记录被降级的 TEACHER 历史账号数量，方便运维感知
            if sql.startswith("UPDATE users SET role='USER'"):
                downgraded = result.rowcount if result is not None else 0
                if downgraded:
                    print(
                        f"[migrate] 审计：{downgraded} 个 TEACHER 历史账号已降级为普通用户",
                        flush=True,
                    )
            # 状态机收敛审计：记录 PROCESSING → GRADING 的作业数量
            if sql.startswith("UPDATE assignments SET status='grading'"):
                migrated = result.rowcount if result is not None else 0
                if migrated:
                    print(
                        f"[migrate] 审计：{migrated} 个作业的 PROCESSING 状态已收敛为 GRADING",
                        flush=True,
                    )
            # 口语测评记录冗余列回填审计：报告历史记录回填条数（0 属正常，幂等）
            if sql.startswith("UPDATE oral_records SET"):
                backfilled = result.rowcount if result is not None else 0
                if backfilled:
                    print(
                        f"[migrate] 审计：{backfilled} 条口语测评历史记录的筛选列已从 detail 回填",
                        flush=True,
                    )
        except OperationalError as e:
            # 仅忽略特定的 MySQL 错误码，其他错误必须抛出（防止真正的迁移失败被静默吞掉）
            err_code = 0
            if hasattr(e, 'orig') and e.orig and hasattr(e.orig, 'args'):
                err_code = e.orig.args[0] if e.orig.args else 0
            if err_code in _SAFE_ERR_CODES:
                print(f"[migrate] SKIP (errno {err_code}): {sql[:50]}...", flush=True)
                continue
            # 非白名单错误：dev 模式告警继续；生产模式必须启动失败，
            # 避免应用在半迁移 schema 上运行导致用户接口全部 500
            message = f"[migrate] WARN (errno {err_code}): {sql[:60]}... -> {e}"
            print(message, flush=True)
            if not settings.DEV_MODE:
                raise RuntimeError(
                    f"数据库迁移失败，应用拒绝在未完全迁移的 schema 上启动：{message}"
                )
        except Exception as e:
            # 非 OperationalError（如连接断开、权限不足）：
            # dev 模式告警继续；生产模式必须启动失败（同上）
            message = f"[migrate] WARN: {sql[:60]}... -> {e}"
            print(message, flush=True)
            if not settings.DEV_MODE:
                raise RuntimeError(
                    f"数据库迁移失败，应用拒绝在未完全迁移的 schema 上启动：{message}"
                )


app = FastAPI(
    title="AI Tutor",
    description="AI 助教系统 API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置：优先用 settings.CORS_ORIGINS，未配置时按 DEV_MODE 给默认值
def _get_cors_origins() -> List[str]:
    if settings.CORS_ORIGINS:
        return [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if settings.DEV_MODE:
        return ["http://localhost:5173", "http://localhost:5174", "http://localhost:5175", "http://localhost:3000"]
    return []

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def add_security_headers(request, call_next):
    """添加安全响应头"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# API Routes
from app.services.file_server import router as files_router
app.include_router(auth.router, prefix="/api/v1")
app.include_router(assignments.router, prefix="/api/v1")
app.include_router(questions.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(error_questions.router, prefix="/api/v1")
app.include_router(ai_tutor.router, prefix="/api/v1")
app.include_router(ai_questions.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(personality.router, prefix="/api/v1")
app.include_router(compositions.router, prefix="/api/v1")
app.include_router(oral_assessments.router, prefix="/api/v1")
app.include_router(usage_stats.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(favorites.router, prefix="/api/v1")
app.include_router(upload_questions.router, prefix="/api/v1")
app.include_router(files_router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


# Dev mode: serve uploaded files from local storage（实现在 services/file_server.py）
from app.services.file_server import serve_local_file as _serve_local_file


@app.get("/api/v1/files/{file_path:path}")
async def serve_local_file_route(file_path: str, request: Request, db: AsyncSession = Depends(get_db)):
    """本地文件服务路由入口（仅 DEV 模式）。"""
    return await _serve_local_file(file_path, request, db)
