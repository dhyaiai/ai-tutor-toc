# 作业批改提速实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将整卷 AI 批改时间提升约 3-4 倍（当前 30 题 ≈ 15 次串行 LLM 调用），全部为**无损优化**，不改变评分质量与输出格式。

**架构：** 四管齐下——(1) 评分批次间并发（`asyncio.Semaphore` 限制并发路数，每批独立 session 天然支持并发写库）；(2) 每批题数 2→3；(3) 消除"每题二次知识点提取 LLM 调用"的隐性瓶颈（评分 prompt 已强制返回 knowledge_points，二次提取纯属冗余）；(4) 整卷评语异步生成，不阻塞"批改完成"状态。

**技术栈：** Python 3.10 + asyncio + FastAPI + SQLAlchemy 2.0 async + React 18 / TanStack Query（仅前端轮询一项配套改动）

**运行方式：** DEV_MODE=true（本地存储 + dev_runner 后台任务）。生产模式（Celery）同步适配。

---

## 背景：瓶颈分析（已定位，勿重复调研）

整卷批改主流程在 `backend/app/tasks/analysis_tasks.py::_do_analyze_inner`（评分循环 686-955 行）：

1. **串行批次循环**：`BATCH_SIZE = grader.MAX_IMAGES_PER_REQUEST`（当前 2），`while batch_start < q_count` 逐批串行，一批的 LLM 调用完成才处理下一批。**整卷并发度实际为 1**。30 题 → 15 次串行视觉 LLM 调用。
2. **隐性二次 LLM 调用**：每题评分落库前调 `extractor.extract(analysis_detail)`（analysis_tasks.py:921、868、1579、1537）。评分评语是【做得好】【存在问题】【改进建议】三段式，`knowledge_extractor.py` 的规则提取模式（`知识点：`/`涉及：`/`考察了`）**几乎必然匹配失败** → 触发 `_llm_extract`（`request_llm_json` attempts=2、timeout=60s，最坏 120s/题）。**每道题实际是 2 次 LLM 调用**。
3. **整卷评语同步生成**：所有评分完成后 `_generate_assignment_summary` 再串一次 LLM（30-60s），阻塞"批改完成"状态落库。
4. 每批仅 2 题：请求往返次数多。

**不做的事**（用户已确认）：不关闭 Qwen3 思考模式（`enable_thinking=false` 可提速 60-75% 但属"有损"，影响复杂题推理深度）；不换模型；不裁剪评分 prompt。

---

### 任务 1：新增批间并发配置项

**文件：**
- 修改：`backend/app/core/config.py:86-89`

- [ ] **步骤 1：在 AI 评分器配置段新增并发路数配置**

```python
    # AI 评分器配置
    GRADER_MAX_OUTPUT_TOKENS: int = 8000
    GRADER_MAX_IMAGES_PER_REQUEST: int = 3   # 每批最多图片数：2→3，减少请求往返次数
    GRADER_MAX_RETRIES: int = 2
    # 批间并发路数：整卷评分时同时进行的批次上限（默认 2 路 × 每批 3 题 = 同时 6 题在飞）。
    # MaaS 专属实例承载能力未知，默认 2 保守起步；实测稳定后可在 .env 调高。
    GRADER_MAX_CONCURRENT_BATCHES: int = 2
```

注意：此步骤同时完成任务 2 的配置项修改（`GRADER_MAX_IMAGES_PER_REQUEST` 2→3），提交时两处一并提交。

- [ ] **步骤 2：验证配置可读**

运行（`backend` 目录）：

```powershell
C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe -c "from app.core.config import get_settings; s=get_settings(); print(s.GRADER_MAX_CONCURRENT_BATCHES, s.GRADER_MAX_IMAGES_PER_REQUEST)"
```

预期：输出 `2 3`。

- [ ] **步骤 3：Commit**

```bash
git add backend/app/core/config.py
git commit -m "feat(config): add batch concurrency setting, raise images per request to 3"
```

---

### 任务 2：批间并发重构 `_do_analyze_inner` 评分循环

**文件：**
- 修改：`backend/app/tasks/analysis_tasks.py:682-955`

**核心思路：** 将 686-955 行的 `while` 串行循环重构为"切块 → 信号量限流并发 → 统一汇总"。并发批次各自用独立 session（原代码已如此），天然无连接冲突；`total_score` / `all_knowledge_points` 改为每批收集后主流程汇总；取消检测改为"批次内抛 `_AnalysisCancelled` + 主流程收尾统一标记"。

- [ ] **步骤 1：在文件顶层（`_RUNNING_ANALYSES` 定义附近）新增取消信号异常类**

在 `analysis_tasks.py` 第 291-293 行 `_RUNNING_ANALYSES` 相关代码之前插入：

```python
class _AnalysisCancelled(Exception):
    """作业分析已被用户终止（并发批次内部信号，供主流程汇总后统一收尾）。"""
    pass
```

- [ ] **步骤 2：新增并发批次处理函数 `_grade_batch_concurrent`**

在 `_do_analyze_inner` 函数**之前**（例如 `recalc_assignment_total` 定义之前）新增独立模块级函数。完整移植原 772-954 行的写库逻辑，评分字段与提示词不变，仅把 `total_score`/`all_knowledge_points` 改为局部累加后返回：

```python
async def _grade_batch_concurrent(
    batch_items: list,
    batch_idx: int,
    batch_total_count: int,
    assignment_id: int,
    subject: str | None,
    personality_directive: str | None,
    grader,
    extractor,
) -> tuple[float, set[str]]:
    """
    并发处理一个评分批次：标记 PROCESSING → 多模态评分 → 取消检测 → 写库。
    返回 (本批总分增量, 本批知识点集合)；检测到用户取消时抛 _AnalysisCancelled。
    每步使用独立 session，避免长 LLM 调用期间占用连接池；并发批次互不干扰。
    """
    # 局部导入（项目风格：函数内导入 DB 依赖；模块级仅 asyncio/Question 已导入）
    from sqlalchemy import delete
    from app.db.session import async_session_factory

    batch_qids = [qid for qid, _ in batch_items]
    batch_images = [img for _, img in batch_items]

    # 标记当前批次为"正在分析"，供前端区分排队中（PENDING）的题目
    async with async_session_factory() as batch_db:
        for qid in batch_qids:
            q = await batch_db.get(Question, qid)
            if q is not None:
                q.status = QuestionStatus.PROCESSING
        await batch_db.commit()

    logger.info("[analyze] Grading batch %d/%d (%d questions)...",
                batch_idx, batch_total_count, len(batch_qids))

    # ── 多模态评分（带超时，预算需容纳一次完整调用 + 一次重试，同原 1320s）──
    try:
        batch_results = await asyncio.wait_for(
            grader.grade_batch(batch_images, subject=subject, personality_directive=personality_directive),
            timeout=1320,
        )
    except asyncio.TimeoutError:
        logger.warning("[analyze] Batch grading timed out for batch %d", batch_idx)
        async with async_session_factory() as batch_db:
            for qid in batch_qids:
                q = await batch_db.get(Question, qid)
                if q is not None:
                    q.status = QuestionStatus.FAILED
                    q.analysis_detail = "评分超时，请重新分析该题"
                    # 清空残留分数/知识点，避免逐题分与作业总分、错题数统计不一致
                    q.score = None
                    q.full_score = None
                    q.knowledge_points = None
            await batch_db.commit()
        return (0.0, set())
    except Exception as e:
        logger.error("[analyze] Batch grading failed for batch %d: %s", batch_idx, e)
        async with async_session_factory() as batch_db:
            for qid in batch_qids:
                q = await batch_db.get(Question, qid)
                if q is not None:
                    q.status = QuestionStatus.FAILED
                    q.analysis_detail = f"评分异常: {str(e)}"
                    q.score = None
                    q.full_score = None
                    q.knowledge_points = None
            await batch_db.commit()
        return (0.0, set())

    # ── 取消检测（写库前，针对在飞批次）──
    # 检测到取消 → 放弃本批结果，本批题目标记"分析已终止"，抛信号让主流程
    # 统一收尾剩余题目（并发下"未处理题目"由主流程按非终态统一标记）
    async with async_session_factory() as batch_db:
        if await _is_analysis_cancelled(batch_db, assignment_id):
            logger.info("[analyze] Assignment %d 批次执行期间被用户终止，放弃本批结果", assignment_id)
            for qid in batch_qids:
                q = await batch_db.get(Question, qid)
                if q is not None:
                    q.status = QuestionStatus.FAILED
                    q.analysis_detail = "分析已终止"
            await batch_db.commit()
            raise _AnalysisCancelled()

    # ── 写库：应用评分结果（支持大题套小题）──
    batch_total = 0.0
    batch_kps: set[str] = set()
    async with async_session_factory() as batch_db:
        batch_questions = []
        for qid in batch_qids:
            q = await batch_db.get(Question, qid)
            if q is not None:
                batch_questions.append(q)

        # 按索引对齐题目与评分结果，而非 zip：评分期间若某题被并发删除，
        # batch_questions 会变短导致 zip 结果整体错位写入错误题目
        for _idx, grade_result in enumerate(batch_results):
            if _idx >= len(batch_questions):
                logger.warning("[analyze] Batch result idx %d exceeds loaded questions (%d), skip",
                               _idx, len(batch_questions))
                break
            question = batch_questions[_idx]
            # ── 幻觉拆分防线（在拆子题分支之前独立判断）──
            if (
                grade_result.sub_questions
                and len(grade_result.sub_questions) > 0
                and _is_choice_parent_illusion(grade_result)
            ):
                _merge_choice_illusion(grade_result)

            if grade_result.sub_questions and len(grade_result.sub_questions) > 0:
                # ── 大题套小题：父题为容器，子题存评分 ──
                sub_list = grade_result.sub_questions

                question.score = None
                question.full_score = None
                question.student_answer = None
                question.correct_answer = None
                question.question_text = grade_result.question_text
                question.question_type = _infer_parent_question_type(
                    grade_result.question_type, sub_list
                )
                question.analysis_detail = f"本大题共 {len(sub_list)} 小题"
                question.confidence_score = grade_result.confidence
                question.status = QuestionStatus.COMPLETED

                # 重建子题前清一次该父题现存子题（当前读 DELETE，见原注释）
                await batch_db.execute(
                    delete(Question).where(Question.parent_id == question.id)
                )

                parent_kps: set[str] = set()
                for idx, sq in enumerate(sub_list):
                    child = Question(
                        assignment_id=question.assignment_id,
                        question_number=question.question_number,
                        parent_id=question.id,
                        sub_question_index=idx,
                        image_url=question.image_url,
                        question_text=sq.question_text,
                        student_answer=sq.student_answer,
                        correct_answer=sq.correct_answer,
                        score=sq.score,
                        full_score=sq.full_score,
                        analysis_detail=sq.analysis_detail or (
                            None if sq.confidence >= 0.3
                            else "AI 未返回有效评分结果，请重新分析该题"
                        ),
                        question_type=sq.question_type or grade_result.question_type,
                        common_mistakes=sq.common_mistakes
                        or _fallback_common_mistakes(sq.analysis_detail)
                        or [],
                        confidence_score=sq.confidence,
                        status=QuestionStatus.COMPLETED if sq.confidence >= 0.3 else QuestionStatus.FAILED,
                        page_index=question.page_index,
                        bbox_x=question.bbox_x,
                        bbox_y=question.bbox_y,
                        bbox_w=question.bbox_w,
                        bbox_h=question.bbox_h,
                    )
                    batch_db.add(child)

                    # 子题知识点（任务 4 会替换为直接使用评分返回的 knowledge_points）
                    child_kps = sq.knowledge_points or []
                    if sq.analysis_detail:
                        try:
                            kps = await extractor.extract(sq.analysis_detail)
                            child.knowledge_points = extractor.merge(child_kps, kps)
                        except Exception:
                            child.knowledge_points = child_kps
                    else:
                        child.knowledge_points = child_kps

                    if sq.score is not None:
                        batch_total += sq.score
                    for kp in (child.knowledge_points or []):
                        name = kp if isinstance(kp, str) else kp.get("name", str(kp))
                        batch_kps.add(name)
                        parent_kps.add(name)

                # 父题知识点 = 所有子题知识点的并集，再精简到约5个
                raw_kps = list(parent_kps) if parent_kps else (grade_result.knowledge_points or [])
                question.knowledge_points = await extractor.trim(
                    raw_kps,
                    context=question.analysis_detail,
                    max_count=5,
                )

            else:
                # ── 普通单题 ──
                question.question_text = grade_result.question_text
                question.student_answer = grade_result.student_answer
                question.correct_answer = grade_result.correct_answer
                question.score = grade_result.score
                question.full_score = grade_result.full_score
                question.analysis_detail = grade_result.analysis_detail
                question.question_type = grade_result.question_type
                question.common_mistakes = grade_result.common_mistakes \
                    or _fallback_common_mistakes(grade_result.analysis_detail) \
                    or []
                question.confidence_score = grade_result.confidence
                question.status = QuestionStatus.COMPLETED if grade_result.confidence >= 0.3 else QuestionStatus.FAILED
                if question.status == QuestionStatus.FAILED and not question.analysis_detail:
                    question.analysis_detail = "AI 未返回有效评分结果，请重新分析该题"

                # 知识点（任务 4 会替换为直接使用评分返回的 knowledge_points）
                if grade_result.analysis_detail:
                    try:
                        kps = await extractor.extract(grade_result.analysis_detail)
                        merged = extractor.merge(grade_result.knowledge_points, kps)
                        question.knowledge_points = await extractor.trim(
                            merged,
                            context=grade_result.analysis_detail,
                            max_count=5,
                        )
                    except Exception:
                        question.knowledge_points = await extractor.trim(
                            grade_result.knowledge_points or [],
                            context=grade_result.analysis_detail,
                            max_count=5,
                        )
                else:
                    question.knowledge_points = await extractor.trim(
                        grade_result.knowledge_points or [],
                        context=None,
                        max_count=5,
                    )

                if question.score is not None:
                    batch_total += question.score
                for kp in (question.knowledge_points or []):
                    name = kp if isinstance(kp, str) else kp.get("name", str(kp))
                    batch_kps.add(name)

        # 必须在 async with 块内 commit（原注释：缩进错位会导致静默失效）
        await batch_db.commit()
    logger.info("[analyze] Batch done (%d questions)", len(batch_questions))
    return (batch_total, batch_kps)
```

- [ ] **步骤 3：替换 `_do_analyze_inner` 中的串行循环（682-955 行）**

将 `_do_analyze_inner` 中从 `total_score = 0` 开始到 `batch_start += BATCH_SIZE` 结束的整个循环块替换为：

```python
        total_score = 0
        all_knowledge_points: set[str] = set()
        q_count = len(question_ids_and_images)
        BATCH_SIZE = grader.MAX_IMAGES_PER_REQUEST

        # ── 批间并发：切块后按信号量限流并发处理（每批独立 session）──
        # 配置 GRADER_MAX_CONCURRENT_BATCHES 控制并发路数（默认 2）。
        # 相比原串行循环，整卷耗时 ≈ 串行耗时 / 并发路数（受 LLM 服务承载约束）。
        from app.core.config import get_settings as _get_settings
        _concurrency = max(1, _get_settings().GRADER_MAX_CONCURRENT_BATCHES)

        batches = [
            question_ids_and_images[i:i + BATCH_SIZE]
            for i in range(0, q_count, BATCH_SIZE)
        ]
        if not batches:
            logger.info("[analyze] No questions to grade for assignment %d", assignment_id)
        else:
            sem = asyncio.Semaphore(_concurrency)

            async def _process_with_sem(batch_idx: int, batch_items):
                async with sem:
                    return await _grade_batch_concurrent(
                        batch_items,
                        batch_idx + 1,
                        len(batches),
                        assignment_id,
                        assignment_subject,
                        personality_directive,
                        grader,
                        extractor,
                    )

            gathered = await asyncio.gather(
                *[_process_with_sem(i, b) for i, b in enumerate(batches)],
                return_exceptions=True,
            )

            # ── 统一汇总各批次结果 ──
            cancelled = False
            for r in gathered:
                if isinstance(r, _AnalysisCancelled):
                    cancelled = True
                    continue
                if isinstance(r, BaseException):
                    logger.error("[analyze] 并发批次异常: %s", r, exc_info=True)
                    continue
                batch_total, batch_kps = r
                total_score += batch_total
                all_knowledge_points |= batch_kps

            # ── 取消收尾：剩余仍非终态的题目标记"分析已终止"──
            # 并发下无法精确定位"未处理题目"，统一按非终态（PENDING/PROCESSING）
            # 标记；已在取消瞬间完成写库的批次保留完成结果（与原串行语义一致）。
            if cancelled:
                from sqlalchemy import select
                async with async_session_factory() as batch_db:
                    result = await batch_db.execute(
                        select(Question).where(
                            Question.assignment_id == assignment_id,
                            Question.status.in_(
                                (QuestionStatus.PENDING, QuestionStatus.PROCESSING)
                            ),
                        )
                    )
                    for q in result.scalars().all():
                        q.status = QuestionStatus.FAILED
                        q.analysis_detail = "分析已终止"
                    await batch_db.commit()
                logger.info("[analyze] Assignment %d 已取消，剩余题目已标记终止", assignment_id)
                return
```

同时删除原循环末尾的 `last_score` 日志（952-954 行）。

- [ ] **步骤 4：验证语法与启动**

运行：

```powershell
C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe -m py_compile app/tasks/analysis_tasks.py
```

预期：无输出（编译通过）。随后启动后端确认无 ImportError。

- [ ] **步骤 5：手动功能验证**

1. 启动后端（`backend` 目录）：`C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe -m uvicorn app.main:app --reload --port 8000`
2. 上传一份 ≥10 题的作业 → 手动切割 → 开始分析。
3. 观察后端日志：应出现 `Grading batch 1/5`、`Grading batch 2/5` 交错出现（并发），而非严格串行。
4. 等待完成，确认：作业状态 `completed`、总分正确、每道题评语/知识点/常见错误完整、大题子题正确。
5. 批量失败场景（可选）：临时把 `.env` 的 `VISION_API_BASE` 改错再触发分析，确认各题标记 FAILED 且作业收敛为 `failed`，随后改回。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/tasks/analysis_tasks.py
git commit -m "perf(analyze): parallelize grading batches with configurable concurrency"
```

---

### 任务 3：去除知识点二次 LLM 调用（核心提速点之二）

**文件：**
- 修改：`backend/app/tasks/analysis_tasks.py:921-933`（普通单题）、`:865-873`（子题）
- 修改：`backend/app/tasks/analysis_tasks.py:1577-1591`（单题重分析普通题）、`:1534-1542`（单题重分析子题）
- 修改：`backend/app/services/knowledge_extractor.py`（`trim()` 去 LLM + 删除 `TRIM_PROMPT`）
- 测试：`backend/tests/test_knowledge_extractor.py`（新建）

**原理：** 评分 prompt（`GRADING_SYSTEM_PROMPT`）已强制 LLM 返回 3-6 个核心 `knowledge_points`。当前代码又对三段式评语调 `extractor.extract()` 二次提取——规则模式（`知识点：`/`涉及：`/`考察了`）对【做得好】【存在问题】【改进建议】格式几乎必然匹配失败，从而触发 `_llm_extract`（每次 1-2 次文本 LLM 调用，最坏 120s/题）。直接使用评分返回的 `knowledge_points` + 规则截断 `trim()`，评分质量不变，省去每题一次 LLM 调用。

- [ ] **步骤 1：修改 `knowledge_extractor.py` 的 `trim()`**

将 `trim()`（170-233 行）整体替换为纯规则截断：

```python
    async def trim(self, knowledge_points: list, context: str | None = None, max_count: int = 5) -> list[dict]:
        """
        将知识点列表精简到指定数量（默认5个左右）。

        纯规则截断，不再调用 LLM：评分 prompt 已强制模型返回 3-6 个核心知识点，
        此处仅处理合并后超量的场景，按原顺序保留前 max_count 个即可。
        （旧实现超过 5 个时调 LLM 精简，是整卷批改每道题额外一次 LLM 调用的来源之一）

        Args:
            knowledge_points: 知识点列表，元素可以是字符串或 {"name": ...} 字典
            context: 兼容保留（不再使用）
            max_count: 最多保留的知识点数量

        Returns:
            精简后的知识点字典列表，每项含 {"name": ...}
        """
        names = self._extract_names(knowledge_points)
        if not names:
            return []
        return [{"name": n} for n in names[:max_count]]
```

同时删除已无引用的 `TRIM_PROMPT` 常量（39-51 行）。

- [ ] **步骤 2：修改 `_do_analyze_inner` 的普通单题知识点逻辑（921-933 行）**

替换为：

```python
                        # 知识点：直接用评分 LLM 已返回的 knowledge_points（评分 prompt
                        # 已强制输出 3-6 个核心知识点），仅规则截断到约5个。
                        # 不再对三段式评语二次 extract（规则提取必然失败 → 触发额外
                        # LLM 调用，每道题多一次最长 120s 的请求，是批改慢的隐形瓶颈）
                        question.knowledge_points = await extractor.trim(
                            grade_result.knowledge_points or [],
                            context=grade_result.analysis_detail,
                            max_count=5,
                        )
```

- [ ] **步骤 3：修改 `_do_analyze_inner` 的子题知识点逻辑（865-873 行）**

替换为：

```python
                            # 子题知识点：直接使用评分返回的 knowledge_points + 规则截断
                            # （同上，消除二次提取 LLM 调用）
                            child.knowledge_points = await extractor.trim(
                                sq.knowledge_points or [],
                                context=sq.analysis_detail,
                                max_count=5,
                            )
```

- [ ] **步骤 4：修改 `_do_reanalyze_inner` 的普通题知识点逻辑（1577-1591 行）**

替换为：

```python
            # 知识点：直接用评分 LLM 返回的 knowledge_points + 规则截断（同整卷分析）
            question.knowledge_points = await extractor.trim(
                gr.knowledge_points or [],
                context=gr.analysis_detail,
                max_count=5,
            )
```

- [ ] **步骤 5：修改 `_do_reanalyze_inner` 的子题知识点逻辑（1534-1542 行）**

替换为：

```python
                child.knowledge_points = await extractor.trim(
                    sq.knowledge_points or [],
                    context=sq.analysis_detail,
                    max_count=5,
                )
```

- [ ] **步骤 6：编写单元测试**

创建 `backend/tests/test_knowledge_extractor.py`：

```python
"""
知识点提取 trim 规则截断的单元测试。
运行方式（在 backend 目录下）：
    python -m unittest tests.test_knowledge_extractor -v
"""

import asyncio
import unittest

from app.services.knowledge_extractor import KnowledgeExtractor


class TestTrim(unittest.TestCase):
    def setUp(self):
        self.extractor = KnowledgeExtractor()
        # 强制 LLM 可用状态，验证规则截断路径不会走 LLM
        self.extractor.llm_enabled = True

    def test_trim_caps_at_max_count(self):
        points = [{"name": f"知识点{i}"} for i in range(10)]
        result = asyncio.run(self.extractor.trim(points, context="测试", max_count=5))
        self.assertEqual([p["name"] for p in result], [f"知识点{i}" for i in range(5)])

    def test_trim_keeps_all_when_under_limit(self):
        points = [{"name": "一元二次方程"}, {"name": "勾股定理"}]
        result = asyncio.run(self.extractor.trim(points, context="测试", max_count=5))
        self.assertEqual([p["name"] for p in result], ["一元二次方程", "勾股定理"])

    def test_trim_empty_returns_empty(self):
        result = asyncio.run(self.extractor.trim([], context="测试", max_count=5))
        self.assertEqual(result, [])

    def test_trim_accepts_strings(self):
        points = ["分数加减法", "通分", "约分", "最简分数", "分数比较", "假分数"]
        result = asyncio.run(self.extractor.trim(points, context="测试", max_count=5))
        self.assertEqual([p["name"] for p in result], points[:5])
```

- [ ] **步骤 7：运行测试验证通过**

```powershell
C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe -m unittest tests.test_knowledge_extractor -v
```

预期：4 个测试全部 PASS。

- [ ] **步骤 8：手动验证整卷批改提速**

按任务 2 步骤 5 的方式上传分析一份作业，确认：
1. 每道题知识点仍正确显示（来自评分返回的 knowledge_points）；
2. 后端日志不再出现 `LLM knowledge extraction failed` 或知识点提取相关请求；
3. 单题批改耗时显著下降（相比任务 2 前）。

- [ ] **步骤 9：Commit**

```bash
git add backend/app/services/knowledge_extractor.py backend/app/tasks/analysis_tasks.py backend/tests/test_knowledge_extractor.py
git commit -m "perf(analyze): drop redundant per-question knowledge extraction LLM call"
```

---

### 任务 4：整卷评语异步生成

**文件：**
- 修改：`backend/app/tasks/analysis_tasks.py`（完成阶段 957-1013 行）
- 修改：`backend/app/tasks/analysis_tasks.py`（新增 Celery 任务包装，放在 `refresh_assignment_summary` 定义之后）
- 前端：`frontend/src/pages/AssignmentManagement/AssignmentDetail/index.tsx`（任务 5 单独处理）

**原理：** 当前评分全部完成后，`_generate_assignment_summary` 同步阻塞 30-60s 才把作业状态落为 `completed`。改为：先提交 `completed` + 总分，再后台触发 `refresh_assignment_summary`（该函数已有 LLM 失败→基础统计评语的兜底，见 1123-1139 行）。用户"批改完成"的感知提前一个评语生成周期。

- [ ] **步骤 1：删除完成阶段同步评语生成的冗余收集**

任务 2 重构后 `all_knowledge_points` 仅服务于同步评语的兜底文本。评语异步化后不再需要，删除 `_grade_batch_concurrent` 中的 `batch_kps` 收集与返回值（改为仅返回 `float` 总分），并同步简化主流程汇总：

`_grade_batch_concurrent` 改动：
- 返回类型改为 `tuple[float]`（或直接 `float`），删除 `batch_kps` 的定义、所有 `batch_kps.add(...)`、`parent_kps` 不受影响（父题知识点仍要收集用于 trim）；
- 函数签名返回注解 `-> float`，`return batch_total`（三处 return：正常、超时、异常分别 `return 0.0`）。

主流程汇总改动：

```python
            # ── 统一汇总各批次结果 ──
            cancelled = False
            for r in gathered:
                if isinstance(r, _AnalysisCancelled):
                    cancelled = True
                    continue
                if isinstance(r, BaseException):
                    logger.error("[analyze] 并发批次异常: %s", r, exc_info=True)
                    continue
                total_score += r
```

删除 `all_knowledge_points: set[str] = set()` 的定义及其合并。

- [ ] **步骤 2：改造 `_do_analyze_inner` 完成阶段（957-1013 行）**

将"提交 completed + 同步生成评语"整体替换为"提交 completed + 后台触发评语"：

```python
        # ── 完成 ──
        # 先提交 COMPLETED 状态与总分，再异步生成 AI 评语（不阻塞"批改完成"感知）。
        # 若此期间进程重启/崩溃，状态已落库为 completed，不会永远卡在"正在分析"。
        async with async_session_factory() as db:
            assignment = await db.get(Assignment, assignment_id)
            if assignment is None:
                return
            # 取消检测：用户已终止（状态 FAILED）时不覆盖取消结果
            if await _is_analysis_cancelled(db, assignment_id):
                logger.info("[analyze] Assignment %d 已被用户终止，跳过完成态提交", assignment_id)
                return
            assignment.status = AssignmentStatus.COMPLETED
            assignment.total_score = total_score
            await db.commit()
            logger.info("[analyze] Stage: COMPLETED (assignment %d)", assignment_id)

        # 评语异步生成：DEV 模式走 dev_runner 后台任务；生产模式走 Celery。
        # refresh_assignment_summary 内部已有 LLM 失败 → 基础统计评语兜底。
        from app.core.config import get_settings as _get_settings
        if _get_settings().DEV_MODE:
            from app.tasks.dev_runner import run_async_in_background
            run_async_in_background(refresh_assignment_summary(assignment_id), assignment_id)
        else:
            if refresh_assignment_summary_task is not None:
                refresh_assignment_summary_task.delay(assignment_id)
            else:
                await refresh_assignment_summary(assignment_id)
```

注意：此替换会删除原 974-1009 行中"获取叶子记录、统计错题、同步 `_generate_assignment_summary`、fallback 文本"整段逻辑（这些已由 `refresh_assignment_summary` 内部完成）。

- [ ] **步骤 3：新增 Celery 任务包装**

在 `refresh_assignment_summary` 函数定义（1075-1148 行）之后、`_mark_failed` 之前，新增：

```python
if celery_app is not None:
    @celery_app.task(bind=True, name="refresh_assignment_summary",
                     soft_time_limit=300, time_limit=360)
    def refresh_assignment_summary_task(self, assignment_id: int):
        """整卷评语生成任务（生产模式，评语异步化后由分析主流程触发）。"""
        logger.info("[refresh_summary] Celery task for assignment %d", assignment_id)
        try:
            _run_async(refresh_assignment_summary(assignment_id))
            return {"status": "completed"}
        except Exception as exc:
            logger.error("[refresh_summary] Celery task failed: %s", exc)
            return {"error": str(exc)}
else:
    refresh_assignment_summary_task = None
```

- [ ] **步骤 4：验证语法与手动功能验证**

1. 运行 `python -m py_compile app/tasks/analysis_tasks.py`，确认编译通过。
2. 启动后端，分析一份作业，观察：
   - 作业状态 **快速** 变为 `completed`（不再等评语）；
   - 后端日志随后出现 `[refresh_summary] Assignment N ai_summary refreshed`；
   - 详情页评语最终出现（配合任务 5 的前端轮询，或手动刷新页面验证）。
3. 断网/改错 LLM_API_KEY 场景（可选）：确认评语兜底为基础统计文本，作业状态不受影响。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/tasks/analysis_tasks.py
git commit -m "perf(analyze): generate assignment summary asynchronously after completion"
```

---

### 任务 5：前端评语轮询配套

**文件：**
- 修改：`frontend/src/pages/AssignmentManagement/AssignmentDetail/index.tsx:31-34`

**原理：** 当前轮询条件 `ACTIVE_STATES.has(status)` 在作业变 `completed` 后停止轮询。评语异步化后，`completed` 时 `ai_summary` 可能尚未生成，需继续轮询直到评语出现。

- [ ] **步骤 1：扩展轮询条件**

将 `refetchInterval` 回调（31-34 行）替换为：

```ts
    // 分析进行中定期轮询刷新状态；completed 但评语尚未生成（评语异步生成）
    // 时继续轮询，直到 ai_summary 出现
    refetchInterval: (query) => {
      const data = query.state.data;
      const status = data?.status;
      if (status && ACTIVE_STATES.has(status)) return 10000;
      if (status === "completed" && !data?.ai_summary) return 10000;
      return false;
    },
```

- [ ] **步骤 2：手动验证**

启动前端（`frontend` 目录）：`npm run dev`。分析一份作业，观察详情页：
1. 作业状态变为 `已完成` 后，页面顶部先不显示"助教有话说"，约 30-60s 后自动出现（无需手动刷新）。
2. 评语出现后轮询停止（浏览器 Network 面板不再有详情轮询请求）。

- [ ] **步骤 3：Commit**

```bash
git add frontend/src/pages/AssignmentManagement/AssignmentDetail/index.tsx
git commit -m "feat(frontend): keep polling detail until async summary appears"
```

---

### 任务 6：全量回归验证

- [ ] **步骤 1：运行全部后端测试**

```powershell
cd backend; C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe -m unittest discover -s tests -v
```

预期：现有 `test_tool_router_classify` 与新 `test_knowledge_extractor` 全部 PASS。

- [ ] **步骤 2：端到端回归清单**

| 场景 | 预期 |
|------|------|
| 30 题整卷分析 | 完成后端日志显示并发批次交错；总耗时较改造前显著下降；总分正确 |
| 单题重分析（普通题） | 状态 PROCESSING→COMPLETED，知识点/评语/常见错误完整 |
| 单题重分析（大题父题） | 子题重建无重复，父题知识点并集正确 |
| 含红笔题目 | 去红版/红笔痕迹版双图逻辑不受影响 |
| 教师备注重分析 | remark 覆盖逻辑不受影响 |
| 分析中点取消 | 作业收敛 `failed`，各题显示"分析已终止"，无题目卡 PROCESSING |
| 作业状态轮询 | 前端 grading→completed 正常，评语异步出现 |
| 单题重分析触发"重新汇总" | re-summarize 接口同步生成评语，行为不变 |

- [ ] **步骤 3：提交收尾（如全部通过）**

本计划全部提交已按任务粒度完成，确认 `git log --oneline -8` 无遗漏即可，无需额外提交。

---

## 自检

**规格覆盖度：**
- 批间并发（P0）：任务 1+2 ✔
- 每批题数提升：任务 1 步骤 1（`GRADER_MAX_IMAGES_PER_REQUEST` 2→3）✔
- 知识点二次 LLM 消除（P0）：任务 3 ✔
- 评语异步（P1）：任务 4+5 ✔
- 生产模式（Celery）适配：任务 2 重构保持任务签名；任务 4 步骤 3 ✔
- 用户确认的"仅无损优化"：未改 `enable_thinking`、未换模型、未裁剪 prompt ✔

**占位符扫描：** 无 TODO/待定；每个步骤含具体代码或验证命令。

**类型一致性：** `_grade_batch_concurrent` 返回 `tuple[float, set[str]]`（任务 2），任务 4 改为 `float` 并在同任务内同步修改主流程汇总，无跨任务引用错误。`refresh_assignment_summary_task` 在任务 4 步骤 2 引用、步骤 3 定义，引用位于函数体内（运行时解析），无定义顺序问题。