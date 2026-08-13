import { useState, useRef, useCallback, useEffect } from "react";
import {
  Card, Tag, Button, Typography, Image, Row, Col, Space, message, Collapse,
} from "antd";
import { EyeOutlined, EyeInvisibleOutlined, StarOutlined, StarFilled } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import { questionService, type SimilarQuestionItem, type SimilarBigQuestion } from "../services/questionService";
import { favoriteService } from "../services/favoriteService";
import type { ErrorQuestionItem, SubQuestionItem } from "../services/errorQuestionService";
import { getScoreRate, isPlaceholderAnswer } from "../utils/helpers";
import MathText from "./MathText";
import { RichText } from "./MarkdownPreview";
import SimilarQuestionCard from "./SimilarQuestionCard";
import SimilarBigQuestionCard from "./SimilarBigQuestionCard";

interface Props {
  item: ErrorQuestionItem;
  /** 收藏初始状态（收藏页传入时恒为 true；缺省读列表接口回显的 is_favorited） */
  isFavorited?: boolean;
  /** 收藏状态切换成功的回调（收藏页用于从列表移除卡片） */
  onToggleFavorite?: (nowFavorited: boolean) => void;
  /** 隐藏题目原图（收藏页只需展示转录文字时传 true） */
  hideImage?: boolean;
  /** 隐藏学生作答痕迹（我的答案/得分/对错状态，收藏页只展示题目本身时传 true）。
   *  收藏页模式下同时启用：大题子题完全平铺、隐藏常见错误、子题答案改为按钮式展示 */
  hideStudentAnswer?: boolean;
  /** 隐藏参考答案（收藏页只展示题目本身时传 true） */
  hideAnswer?: boolean;
}

const POLL_INTERVAL = 2000;
// 轮询总时长与后端任务时长对齐：普通题批量生成 3 题，单题超时 240s，最长 12 分钟；
// 原 5 分钟会在后端还没跑完时就提前放弃，导致结果不可达（用户看到占位卡）。
// 这里放宽到 15 分钟（后端 12 分钟 + 缓冲），轮询期间逐题更新，体验不受影响。
const MAX_POLL_TIME = 900000;
const CARD_COUNT = 3;

/** 生成一个失败占位题，确保卡片显示独立重试按钮 */
function makeFailedPlaceholder(index: number): SimilarQuestionItem {
  return {
    id: -1 - index,
    question_text: "生成失败，请点击换一题",
    answer: "",
    knowledge_point: "",
    difficulty: "medium",
    question_type: "",
    options: [],
  };
}

/** 判断子题题型标签（模块级，供 ErrorSubQuestionCard 使用） */
function getChildTypeTag(child: SubQuestionItem) {
  const qt = child.question_type;
  if (!qt) return null;
  if (qt.includes("多选")) return <Tag color="red" style={{ fontSize: 11 }}>多选题</Tag>;
  if (qt.includes("选")) return <Tag color="blue" style={{ fontSize: 11 }}>单选题</Tag>;
  return <Tag color="purple" style={{ fontSize: 11 }}>{qt}</Tag>;
}

/**
 * 大题子题卡片（独立子组件以持有自己的 showAnswer 状态）。
 * - 收藏页模式（hideStudentAnswer）：紧凑块排版（不套独立 Card，与父题合成一个切块），
 *   不展示学生作答痕迹，"查看答案"按钮展开后仅显示正确答案 + 解析。
 *   注意：错题（作业题）没有题目解析数据，analysis_detail 是 AI 评分评语，不作为解析展示，显示"无"
 * - 其他页面：保持原逻辑（独立 Card + 直接展示正确答案文本）
 */
function ErrorSubQuestionCard({
  child,
  hideStudentAnswer,
  hideAnswer,
}: {
  child: SubQuestionItem;
  hideStudentAnswer?: boolean;
  hideAnswer?: boolean;
}) {
  const [showAnswer, setShowAnswer] = useState(false);
  // 隐藏学生作答时不算对错（无作答数据可依赖）
  const isError =
    !hideStudentAnswer &&
    child.score != null &&
    child.full_score != null &&
    (child.score as number) < (child.full_score as number);

  /* ── 收藏页模式：紧凑块排版（一个大题切块内的小题块） ── */
  if (hideStudentAnswer) {
    return (
      <div style={{ padding: "10px 0", borderBottom: "1px dashed #f0f0f0" }}>
        {/* 题号行 + 查看答案按钮（答案/解析都无内容时不显示按钮） */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 4,
          }}
        >
          <Space size={4}>
            <Typography.Text strong style={{ fontSize: 13 }}>
              小题 {child.sub_question_index + 1}
            </Typography.Text>
            {getChildTypeTag(child)}
          </Space>
          {child.correct_answer && !isPlaceholderAnswer(child.correct_answer) && (
            <Button
              type="link"
              size="small"
              icon={showAnswer ? <EyeInvisibleOutlined /> : <EyeOutlined />}
              onClick={() => setShowAnswer(!showAnswer)}
              style={{ padding: "0 4px", fontSize: 13 }}
            >
              {showAnswer ? "折叠答案" : "查看答案"}
            </Button>
          )}
        </div>
        {/* 转录后的子题题干文本 */}
        {child.question_text && (
          <RichText
            content={child.question_text}
            style={{ display: "block", marginBottom: 8, fontSize: 13 }}
          />
        )}
        {/* 答案展开区：仅正确答案（错题无题目解析，显示"无"） */}
        {showAnswer && (
          <div
            style={{
              marginTop: 4,
              padding: 12,
              background: "#fafafa",
              borderRadius: 6,
              border: "1px solid #e8e8e8",
            }}
          >
            <Typography.Text strong style={{ fontSize: 13, color: "#52c41a" }}>
              正确答案：
            </Typography.Text>
            <RichText content={child.correct_answer} style={{ fontSize: 13 }} />
            <div style={{ marginTop: 8 }}>
              <Typography.Text strong style={{ fontSize: 13, color: "#722ed1" }}>
                解析：
              </Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                无
              </Typography.Text>
            </div>
          </div>
        )}
        {child.knowledge_points && (
          <div style={{ marginTop: 4 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              知识点：
            </Typography.Text>
            {(Array.isArray(child.knowledge_points)
              ? child.knowledge_points as Array<string | { name: string }>
              : []
            ).map((kp: string | { name: string }, i: number) => {
              const name = typeof kp === "string" ? kp : kp.name;
              return (
                <Tag key={`${name}-${i}`} color="blue" style={{ fontSize: 10, marginTop: 2, fontWeight: 600 }}>
                  <MathText content={name} />
                </Tag>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  /* ── 其他页面（错题重做）：保持原逻辑（独立 Card） ── */
  return (
    <Card
      size="small"
      style={{
        marginBottom: 8,
        borderLeft: isError ? "3px solid #ff4d4f" : "3px solid #52c41a",
      }}
      title={
        <Space size={4}>
          <Typography.Text style={{ fontSize: 13 }}>
            小题 {child.sub_question_index + 1}
          </Typography.Text>
          {getChildTypeTag(child)}
          {isError ? (
            <Tag color="error" style={{ fontSize: 11 }}>错误</Tag>
          ) : (
            <Tag color="success" style={{ fontSize: 11 }}>正确</Tag>
          )}
        </Space>
      }
    >
      {/* 转录后的子题题干文本：错题重做板块不显示结构化转录内容（原图已直观展示题目） */}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            得分：
          </Typography.Text>
          <Typography.Text style={{ fontSize: 12 }}>
            {child.score != null ? `${child.score} / ${child.full_score}` : "-"}
          </Typography.Text>
        </div>
        <div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            得分率：
          </Typography.Text>
          <Typography.Text style={{ fontSize: 12 }}>
            {getScoreRate(child.score as number | null, child.full_score as number | null)}
          </Typography.Text>
        </div>
        {child.student_answer && (
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12, color: "#ff4d4f" }}>
              我的答案：
            </Typography.Text>
            <MathText content={child.student_answer} style={{ fontSize: 12 }} />
          </div>
        )}
        {!hideAnswer && child.correct_answer && (
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12, color: "#52c41a" }}>
              正确答案：
            </Typography.Text>
            <RichText content={child.correct_answer} style={{ fontSize: 12 }} />
          </div>
        )}
      </div>
      {child.knowledge_points && (
        <div style={{ marginTop: 4 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            知识点：
          </Typography.Text>
          {(Array.isArray(child.knowledge_points)
            ? child.knowledge_points as Array<string | { name: string }>
            : []
          ).map((kp: string | { name: string }, i: number) => {
            const name = typeof kp === "string" ? kp : kp.name;
            return (
              <Tag key={`${name}-${i}`} color="blue" style={{ fontSize: 10, marginTop: 2, fontWeight: 600 }}>
                <MathText content={name} />
              </Tag>
            );
          })}
        </div>
      )}
    </Card>
  );
}

export default function ErrorQuestionCard({ item, isFavorited, onToggleFavorite, hideImage, hideStudentAnswer, hideAnswer }: Props) {
  // 收藏状态（本地 state，点击后即时切换；初始值优先取 prop，缺省读列表接口回显）
  const [fav, setFav] = useState(isFavorited ?? item.is_favorited ?? false);
  const [favPending, setFavPending] = useState(false);
  const queryClient = useQueryClient();

  // 小题目（普通题）状态
  const [similarCards, setSimilarCards] = useState<Array<SimilarQuestionItem | null> | null>(null);
  // 大题目（大题）状态
  const [similarBigQuestion, setSimilarBigQuestion] = useState<SimilarBigQuestion | null>(null);
  const [bigGenerating, setBigGenerating] = useState(false);

  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAnswer, setShowAnswer] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 换一题（replace）轮询独立 ref：与批量生成轮询互不干扰，
  // 避免换题时把仍在进行中的批量轮询清掉导致卡片停在占位态
  const replacePollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sourceId = item.id as number;
  const isBigQuestion = (item.is_big_question as boolean) || false;
  const children: SubQuestionItem[] = (item.children as SubQuestionItem[]) || [];

  const clearPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const clearReplacePolling = useCallback(() => {
    if (replacePollingRef.current) {
      clearInterval(replacePollingRef.current);
      replacePollingRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      clearPolling();
      clearReplacePolling();
    };
  }, [clearPolling, clearReplacePolling]);

  /** 收藏 / 取消收藏（错题锚点 = 卡片 id，即父题或独立题 id） */
  const handleToggleFavorite = useCallback(async () => {
    if (favPending) return;
    setFavPending(true);
    const target = !fav;
    try {
      if (target) {
        await favoriteService.add("error", sourceId);
      } else {
        await favoriteService.remove("error", sourceId);
      }
      setFav(target);
      onToggleFavorite?.(target);
      // 让错题列表页与收藏页的星标状态同步（react-query 缓存一致性）
      queryClient.invalidateQueries({ queryKey: ["errorQuestions"] });
      queryClient.invalidateQueries({ queryKey: ["favorites"] });
    } catch (e: any) {
      message.error("收藏操作失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    } finally {
      setFavPending(false);
    }
  }, [fav, favPending, sourceId, onToggleFavorite, queryClient]);

  /** 收藏星星按钮（标题行尾部，实心金色 = 已收藏） */
  const favoriteButton = (
    <Button
      type="text"
      size="small"
      icon={fav ? <StarFilled style={{ color: "#faad14" }} /> : <StarOutlined />}
      loading={favPending}
      onClick={handleToggleFavorite}
      title={fav ? "取消收藏" : "收藏"}
      style={{ marginLeft: 8 }}
    />
  );

  /** 组件挂载时检查后端是否已有缓存结果（页面切换后恢复） */
  useEffect(() => {
    let cancelled = false;
    const restoreFromCache = async () => {
      try {
        const res = await questionService.getSimilarResult(sourceId);
        if (cancelled) return;

        if (res.status === "completed") {
          if (res.is_big_question && res.similar_questions && typeof res.similar_questions === "object" && !Array.isArray(res.similar_questions)) {
            // 大题：恢复缓存的类似大题
            setSimilarBigQuestion(res.similar_questions as unknown as SimilarBigQuestion);
          } else if (Array.isArray(res.similar_questions) && res.similar_questions.length > 0) {
            // 普通题：恢复缓存的同类题
            const restored = Array(CARD_COUNT).fill(null);
            for (let i = 0; i < Math.min(res.similar_questions.length, CARD_COUNT); i++) {
              restored[i] = res.similar_questions[i];
            }
            setSimilarCards(restored);
          }
        } else if (res.status === "processing") {
          // 有进行中的任务，恢复轮询
          const startTime = Date.now();
          if (res.is_big_question) {
            setBigGenerating(true);
            pollingRef.current = setInterval(async () => {
              try {
                const pollRes = await questionService.getSimilarResult(sourceId);
                if (pollRes.is_big_question && pollRes.similar_questions && typeof pollRes.similar_questions === "object" && !Array.isArray(pollRes.similar_questions)) {
                  setSimilarBigQuestion(pollRes.similar_questions as unknown as SimilarBigQuestion);
                }
                if (pollRes.status === "completed") {
                  clearPolling();
                  setBigGenerating(false);
                } else if (pollRes.status === "failed") {
                  clearPolling();
                  setBigGenerating(false);
                  setError(pollRes.error || "生成失败");
                }
                if (Date.now() - startTime > MAX_POLL_TIME) {
                  clearPolling();
                  setBigGenerating(false);
                }
              } catch { /* ignore */ }
            }, POLL_INTERVAL);
          } else {
            setGenerating(true);
            pollingRef.current = setInterval(async () => {
              try {
                const pollRes = await questionService.getSimilarResult(sourceId);
                const questions = pollRes.similar_questions;
                if (Array.isArray(questions) && questions.length > 0) {
                  setSimilarCards((prev) => {
                    const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
                    for (let i = 0; i < Math.min(questions.length, CARD_COUNT); i++) {
                      updated[i] = questions[i];
                    }
                    return updated;
                  });
                }
                if (pollRes.status === "completed") {
                  clearPolling();
                  setGenerating(false);
                } else if (pollRes.status === "failed") {
                  clearPolling();
                  setGenerating(false);
                  setError(pollRes.error || "生成失败");
                }
                if (Date.now() - startTime > MAX_POLL_TIME) {
                  clearPolling();
                  setGenerating(false);
                }
              } catch { /* ignore */ }
            }, POLL_INTERVAL);
          }
        }
      } catch {
        // 静默失败，用户可手动点击生成
      }
    };

    restoreFromCache();
    return () => { cancelled = true; };
  }, [sourceId, clearPolling]);

  /** 普���题：生成3道同类题 */
  const handleGenerate = useCallback(async () => {
    setGenerating(true);
    setSimilarCards(Array(CARD_COUNT).fill(null));
    setError(null);
    clearPolling();

    try {
      await questionService.generateSimilar(sourceId);
      const startTime = Date.now();

      pollingRef.current = setInterval(async () => {
        try {
          const res = await questionService.getSimilarResult(sourceId);
          const questions = res.similar_questions;

          if (Array.isArray(questions) && questions.length > 0) {
            setSimilarCards((prev) => {
              const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
              for (let i = 0; i < Math.min(questions.length, CARD_COUNT); i++) {
                updated[i] = questions[i];
              }
              return updated;
            });
          }

          if (res.status === "completed") {
            clearPolling();
            setGenerating(false);
          } else if (res.status === "failed") {
            clearPolling();
            setError(res.error || "生成失败");
            setGenerating(false);
            setSimilarCards((prev) => {
              const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
              for (let i = 0; i < CARD_COUNT; i++) {
                if (!updated[i]) updated[i] = makeFailedPlaceholder(i);
              }
              return updated;
            });
          }

          if (Date.now() - startTime > MAX_POLL_TIME) {
            clearPolling();
            setError("生成超时，请稍后重试");
            setGenerating(false);
            setSimilarCards((prev) => {
              const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
              for (let i = 0; i < CARD_COUNT; i++) {
                if (!updated[i]) updated[i] = makeFailedPlaceholder(i);
              }
              return updated;
            });
          }
        } catch {
          // 单次轮询失败忽略
        }
      }, POLL_INTERVAL);
    } catch (e: any) {
      clearPolling();
      const detail = e?.response?.data?.detail || e?.message || "未知错误";
      const status = e?.response?.status || "";
      setError(`创建任务失败(题目ID=${sourceId}, HTTP ${status}): ${detail}`);
      setGenerating(false);
    }
  }, [sourceId, clearPolling]);

  /** 大题：生成1道类似大题 */
  const handleGenerateBig = useCallback(async () => {
    setBigGenerating(true);
    setSimilarBigQuestion(null);
    setError(null);
    clearPolling();

    try {
      await questionService.generateSimilar(sourceId);
      const startTime = Date.now();

      pollingRef.current = setInterval(async () => {
        try {
          const res = await questionService.getSimilarResult(sourceId);

          if (res.is_big_question && res.similar_questions && typeof res.similar_questions === "object" && !Array.isArray(res.similar_questions)) {
            const bigQ = res.similar_questions as unknown as SimilarBigQuestion;
            setSimilarBigQuestion(bigQ);
          }

          if (res.status === "completed") {
            clearPolling();
            setBigGenerating(false);
          } else if (res.status === "failed") {
            clearPolling();
            setError(res.error || "生成失败");
            setBigGenerating(false);
          }

          if (Date.now() - startTime > MAX_POLL_TIME) {
            clearPolling();
            setError("生成超时，请稍后重试");
            setBigGenerating(false);
          }
        } catch {
          // 单次轮询失败忽略
        }
      }, POLL_INTERVAL);
    } catch (e: any) {
      clearPolling();
      const detail = e?.response?.data?.detail || e?.message || "未知错误";
      setError(`创建任务失败(题目ID=${sourceId}, HTTP ${e?.response?.status || ""}): ${detail}`);
      setBigGenerating(false);
    }
  }, [sourceId, clearPolling]);

  /** 普通题：换一题（异步任务 + 轮询 replace 结果）。
   * 原实现同步等待 LLM（后端最长 360s，前端 axios 120s 必超时），表现为"换一题没反应"。
   * 现在：先置卡片为加载态 → 创建后台任务（202 快速返回）→ 轮询 replace 字段替换卡片；
   * 失败/超时恢复原题，不会让卡片停留在无反馈的悬挂状态。 */
  const handleReplace = useCallback(async (index: number) => {
    // 记住原题，失败/超时时恢复
    const oldQ = similarCards?.[index] ?? null;
    clearReplacePolling();
    setSimilarCards((prev) => {
      const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
      updated[index] = null; // 卡片显示 Skeleton 加载态
      return updated;
    });

    try {
      await questionService.generateSimilarSingle(sourceId, "medium", index);
    } catch (e: any) {
      // 恢复原题（如批量任务进行中后端返回 409 时，批量轮询仍会继续更新卡片）
      setSimilarCards((prev) => {
        const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
        updated[index] = oldQ;
        return updated;
      });
      message.error("换题失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
      return;
    }

    // 轮询 replace 任务结果（独立 ref，不清批量轮询）
    const startTime = Date.now();
    replacePollingRef.current = setInterval(async () => {
      try {
        const res = await questionService.getSimilarResult(sourceId);
        const rep = res.replace;
        if (rep && rep.status === "completed") {
          clearReplacePolling();
          // 普通题：用 replace.question 替换对应卡片（防大题误判）
          if (rep.question && !("is_big_question" in rep.question)) {
            const target = rep.index >= 0 ? rep.index : index;
            setSimilarCards((prev) => {
              const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
              updated[target] = rep.question as SimilarQuestionItem;
              return updated;
            });
          }
        } else if (rep && rep.status === "failed") {
          clearReplacePolling();
          setSimilarCards((prev) => {
            const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
            updated[index] = oldQ;
            return updated;
          });
          message.error("换题失败: " + (rep.error || "生成失败"));
        } else if (Date.now() - startTime > MAX_POLL_TIME) {
          clearReplacePolling();
          setSimilarCards((prev) => {
            const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
            updated[index] = oldQ;
            return updated;
          });
          message.warning("换题超时，请稍后重试");
        }
      } catch {
        // 单次轮询失败忽略
      }
    }, POLL_INTERVAL);
  }, [sourceId, similarCards, clearReplacePolling]);

  /** 大题：换一题（指定难度，异步任务 + 轮询 replace 结果，逻辑同普通题） */
  const handleReplaceBig = useCallback(async (difficulty: string) => {
    const oldQ = similarBigQuestion;
    clearReplacePolling();
    setSimilarBigQuestion(null); // 显示 Spin 加载态

    try {
      await questionService.generateSimilarSingle(sourceId, difficulty, -1);
    } catch (e: any) {
      setSimilarBigQuestion(oldQ);
      message.error("换题失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
      return;
    }

    const startTime = Date.now();
    replacePollingRef.current = setInterval(async () => {
      try {
        const res = await questionService.getSimilarResult(sourceId);
        const rep = res.replace;
        if (rep && rep.status === "completed") {
          clearReplacePolling();
          // 大题：用 replace.question 整体替换（防普通题误判）
          if (rep.question && "is_big_question" in rep.question) {
            setSimilarBigQuestion(rep.question as SimilarBigQuestion);
          }
        } else if (rep && rep.status === "failed") {
          clearReplacePolling();
          setSimilarBigQuestion(oldQ);
          message.error("换题失败: " + (rep.error || "生成失败"));
        } else if (Date.now() - startTime > MAX_POLL_TIME) {
          clearReplacePolling();
          setSimilarBigQuestion(oldQ);
          message.warning("换题超时，请稍后重试");
        }
      } catch {
        // 单次轮询失败忽略
      }
    }, POLL_INTERVAL);
  }, [sourceId, similarBigQuestion, clearReplacePolling]);

  // ── 已开始 = 已点击过按钮（不管成功失败）──
  const started = similarCards !== null || similarBigQuestion !== null;
  const hasError = !!error;

  // ═══════════════════════════════════════════
  // 大题渲染模式
  // ═══════════════════════════════════════════
  if (isBigQuestion) {
    const errorCount = (item.error_count as number) || 0;
    const totalCount = (item.total_count as number) || children.length;

    // 题型标签只打在大题整体：父题题型 + 各子题题型去重合并
    // （如子题分别为"证明题""计算题"时，合并显示两个标签，不再分配给每个小问）
    const mergedTypes = Array.from(
      new Set(
        [item.question_type, ...children.map((c) => c.question_type)].filter(Boolean) as string[]
      )
    );

    // 父题题干是否已完整包含所有小问（含 "(1)(2)" 等小问标记）？
    // 是 → 顶部题干区已完整展示全部题目内容（含所有小问原文），子题行不再重复渲染，
    //      避免"顶部完整题干 + 下方子题行"的重复文本；
    // 否（父题题干为空或只有公共题干，如早期数据）→ 保留子题行展示小问内容
    const parentTextCoversChildren =
      !!item.question_text &&
      /[\(（]\s*\d+\s*[\)\）]|第[一二三四五六七八九十百0-9]+[问題]/.test(item.question_text);

    return (
      <Card size="small">
        <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
          {!hideImage && item.image_url && (
            <div style={{ maxWidth: 240 }}>
              <Image
                src={item.image_url as string}
                alt={`第${item.question_number}大题`}
                width={160}
                style={{ borderRadius: 4 }}
              />
            </div>
          )}
          <div style={{ flex: 1 }}>
            <Typography.Text strong>
              第 {item.question_number as number} 大题 — {item.assignment_name as string}
            </Typography.Text>
            {mergedTypes.map((t) => (
              <Tag key={t} color="purple" style={{ marginLeft: 8 }}>{t}</Tag>
            ))}
            {!hideStudentAnswer && (
              <Tag color="orange" style={{ marginLeft: 4 }}>
                共 {totalCount} 小题，错 {errorCount} 题
              </Tag>
            )}
            {favoriteButton}
            {!hideStudentAnswer && (
              <div style={{ marginTop: 8 }}>
                <Typography.Text>
                  得分率：{getScoreRate(null, null, item.score_rate as number | undefined)}
                </Typography.Text>
              </div>
            )}

            {item.knowledge_points && (
              <div style={{ marginTop: 4 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>知识点：</Typography.Text>
                {(Array.isArray(item.knowledge_points)
                  ? (item.knowledge_points as Array<string | { name: string }>)
                  : []
                ).map((kp, i: number) => {
                  const name = typeof kp === "string" ? kp : kp.name;
                  return (
                    <Tag key={`${name}-${i}`} color="blue" style={{ marginTop: 2, fontWeight: 600 }}>
                      <MathText content={name} />
                    </Tag>
                  );
                })}
              </div>
            )}

            <div style={{ marginTop: 8 }}>
              <Button
                type={bigGenerating ? "default" : "primary"}
                size="small"
                loading={bigGenerating}
                onClick={handleGenerateBig}
                disabled={bigGenerating}
              >
                {similarBigQuestion ? "重新生成同类大题" : "AI 生成同类大题"}
              </Button>
            </div>
          </div>
        </div>

        {/* 子题列表：收藏页模式（hideStudentAnswer）整体展示——题干在上、小题换行排列、
            一个"查看答案"按钮统一展开全部子题答案；其他页面保持折叠面板 */}
        {children.length > 0 && (
          hideStudentAnswer ? (
            <div style={{ marginTop: 12 }}>
              {/* 大题题干（转录文本，有值才展示） */}
              {item.question_text && (
                <div
                  style={{
                    padding: 12,
                    background: "#fafafa",
                    borderRadius: 6,
                    border: "1px solid #e8e8e8",
                    marginBottom: 12,
                  }}
                >
                  <RichText content={item.question_text} style={{ fontSize: 13, lineHeight: 1.8 }} />
                </div>
              )}
              {/* 各小题：换行展示，不再使用独立卡片切块。
                  父题题干已完整包含小问（含 (1)(2) 标记）时不重复渲染——题干只出现一次；
                  父题题干缺失/只有公共题干时仍展示子题行，保证题目内容不丢失 */}
              {!parentTextCoversChildren &&
                children.map((child: SubQuestionItem, idx: number) => (
                  <div key={child.id as number} style={{ marginBottom: 8, lineHeight: 1.8 }}>
                    <Space size={4} align="start">
                      <Typography.Text strong style={{ fontSize: 13 }}>
                        ({idx + 1})
                      </Typography.Text>
                      {/* 题型标签只打在大题整体（标题区合并展示），子题行不再单独打标签 */}
                      {child.question_text && (
                        <RichText content={child.question_text} style={{ fontSize: 13 }} />
                      )}
                    </Space>
                  </div>
                ))}
              {/* 统一"查看答案"按钮：一次性展开/折叠所有子题的正确答案 */}
              {children.some(
                (c: SubQuestionItem) => c.correct_answer && !isPlaceholderAnswer(c.correct_answer)
              ) && (
                <div style={{ marginTop: 8 }}>
                  <Button
                    type="link"
                    size="small"
                    icon={showAnswer ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                    onClick={() => setShowAnswer(!showAnswer)}
                    style={{ padding: "0 4px", fontSize: 13 }}
                  >
                    {showAnswer ? "折叠答案" : "查看答案"}
                  </Button>
                  {showAnswer && (
                    <div
                      style={{
                        marginTop: 8,
                        padding: 12,
                        background: "#fafafa",
                        borderRadius: 6,
                        border: "1px solid #e8e8e8",
                      }}
                    >
                      {children.map((child: SubQuestionItem, idx: number) => (
                        <div
                          key={child.id as number}
                          style={{
                            marginBottom: idx < children.length - 1 ? 8 : 0,
                            paddingBottom: idx < children.length - 1 ? 8 : 0,
                            borderBottom:
                              idx < children.length - 1 ? "1px dashed #e8e8e8" : "none",
                          }}
                        >
                          <Typography.Text strong style={{ fontSize: 12, color: "#8c8c8c" }}>
                            ({idx + 1}){" "}
                          </Typography.Text>
                          <Typography.Text strong style={{ fontSize: 13, color: "#52c41a" }}>
                            正确答案：
                          </Typography.Text>
                          <RichText
                            content={child.correct_answer || "—"}
                            style={{ fontSize: 13 }}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <Collapse
              style={{ marginTop: 12 }}
              items={[
                {
                  key: "sub-questions",
                  label: <Typography.Text strong style={{ fontSize: 13 }}>小题详情（{children.length} 题）</Typography.Text>,
                  children: (
                    <div>
                      {children.map((child: SubQuestionItem) => (
                        <ErrorSubQuestionCard key={child.id as number} child={child} />
                      ))}
                    </div>
                  ),
                },
              ]}
            />
          )
        )}

        {/* 类大题展示 */}
        {hasError && (
          <Typography.Text type="danger" style={{ display: "block", marginTop: 12, fontSize: 13 }}>
            {error}
          </Typography.Text>
        )}
        {started && (
          <SimilarBigQuestionCard
            question={similarBigQuestion}
            questionId={sourceId}
            onReplace={handleReplaceBig}
          />
        )}
      </Card>
    );
  }

  // ═══════════════════════════════════════════
  // 普通独立题渲染模式（保持原有逻辑）
  // ═══════════════════════════════════════════
  const allFilled = started && similarCards!.every((c) => c !== null);

  return (
    <Card size="small">
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        {!hideImage && item.image_url && (
          <div style={{ maxWidth: 240 }}>
            <Image
              src={item.image_url as string}
              alt={`第${item.question_number}题`}
              width={160}
              style={{ borderRadius: 4 }}
            />
          </div>
        )}
        <div style={{ flex: 1 }}>
          <Typography.Text strong>
            第 {item.question_number as number} 题 — {item.assignment_name as string}
          </Typography.Text>
          {item.question_type && (
            <Tag color="purple" style={{ marginLeft: 8 }}>{item.question_type as string}</Tag>
          )}
          {favoriteButton}
          {/* 转录后的题干文本：仅收藏页模式（hideStudentAnswer）展示；
              错题重做板块不显示结构化转录内容（原图已直观展示题目） */}
          {hideStudentAnswer && item.question_text && (
            <RichText
              content={item.question_text}
              style={{ display: "block", marginTop: 8, fontSize: 13 }}
            />
          )}
          {!hideStudentAnswer && (
            <div style={{ marginTop: 8 }}>
              <Typography.Text>
                得分率：{getScoreRate(item.score as number | null, item.full_score as number | null)}
              </Typography.Text>
            </div>
          )}
          {/* 原题答案折叠区域（收藏页隐藏作答时仅展示正确答案 + 解析，不展示我的答案/得分） */}
          {!hideAnswer && (item.correct_answer || item.analysis_detail || (!hideStudentAnswer && item.student_answer)) && (
            <div style={{ marginTop: 8 }}>
              <Button
                type="link"
                size="small"
                icon={showAnswer ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                onClick={() => setShowAnswer(!showAnswer)}
                style={{ padding: "0 4px", fontSize: 13 }}
              >
                {showAnswer ? "折叠答案" : "查看答案"}
              </Button>
              {showAnswer && (
                <div
                  style={{
                    marginTop: 8,
                    padding: 12,
                    background: "#fafafa",
                    borderRadius: 6,
                    border: "1px solid #e8e8e8",
                  }}
                >
                  {!hideStudentAnswer && item.student_answer && (
                    <div style={{ marginBottom: item.correct_answer || item.analysis_detail ? 10 : 0 }}>
                      <Typography.Text strong style={{ fontSize: 13, color: "#ff4d4f" }}>
                        我的答案：
                      </Typography.Text>
                      <MathText content={item.student_answer} style={{ fontSize: 13 }} />
                    </div>
                  )}
                  {item.correct_answer && !isPlaceholderAnswer(item.correct_answer) && (
                    <div>
                      <Typography.Text strong style={{ fontSize: 13, color: "#52c41a" }}>
                        正确答案：
                      </Typography.Text>
                      <RichText content={item.correct_answer} style={{ fontSize: 13 }} />
                    </div>
                  )}
                  {/* 解析：错题（作业题）没有题目解析数据，analysis_detail 是 AI 评分评语不作为解析展示；
                      收藏页模式显示"无"，其他页面（错题重做）保留展示 AI 评分分析 */}
                  {hideStudentAnswer ? (
                    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #d9d9d9" }}>
                      <Typography.Text strong style={{ fontSize: 13, color: "#722ed1" }}>
                        解析：
                      </Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        无
                      </Typography.Text>
                    </div>
                  ) : item.analysis_detail ? (
                    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #d9d9d9" }}>
                      <Typography.Text strong style={{ fontSize: 13, color: "#722ed1" }}>
                        分析：
                      </Typography.Text>
                      <RichText content={item.analysis_detail} style={{ display: "block", fontSize: 13, marginTop: 4 }} />
                    </div>
                  ) : null}
                  {!hideStudentAnswer && item.score != null && item.full_score != null && (
                    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #d9d9d9" }}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        得分：{item.score as number}/{item.full_score as number}
                      </Typography.Text>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          {item.knowledge_points && (
            <div style={{ marginTop: 4 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>知识点：</Typography.Text>
              {(Array.isArray(item.knowledge_points)
                ? (item.knowledge_points as Array<string | { name: string }>)
                : []
              ).map((kp, i: number) => {
                const name = typeof kp === "string" ? kp : kp.name;
                return (
                  <Tag key={`${name}-${i}`} color="blue" style={{ marginTop: 2, fontWeight: 600 }}>
                    <MathText content={name} />
                  </Tag>
                );
              })}
            </div>
          )}
          {/* 常见错误：收藏页模式（hideStudentAnswer）不展示 */}
          {!hideStudentAnswer && item.common_mistakes && Array.isArray(item.common_mistakes) && (item.common_mistakes as string[]).length > 0 && (
            <div style={{ marginTop: 4 }}>
              <Typography.Text type="warning" style={{ fontSize: 12 }}>常见错误：</Typography.Text>
              {(item.common_mistakes as string[]).map((m: string, i: number) => (
                <Tag key={i} color="orange" style={{ marginTop: 2 }}><MathText content={m} /></Tag>
              ))}
            </div>
          )}
          <div style={{ marginTop: 8 }}>
            <Button
              type={generating ? "default" : "primary"}
              size="small"
              loading={generating}
              onClick={handleGenerate}
              disabled={generating}
              style={generating ? {
                color: "#52c41a",
                borderColor: "#52c41a",
              } : undefined}
            >
              <span style={generating ? { color: "#52c41a" } : undefined}>
                {allFilled ? "重新生成" : "AI 生成同类题"}
              </span>
            </Button>
          </div>
        </div>
      </div>

      {/* 同类题卡片区域 */}
      {started && (
        <>
          {hasError && (
            <Typography.Text type="danger" style={{ display: "block", marginTop: 12, fontSize: 13 }}>
              {error}
            </Typography.Text>
          )}
          <Row gutter={[12, 12]} style={{ marginTop: hasError ? 8 : 16 }}>
            {similarCards!.map((q, i) => (
              <Col key={i} xs={24} sm={12} md={8}>
                <SimilarQuestionCard
                  index={i}
                  question={q}
                  questionId={sourceId}
                  onReplace={handleReplace}
                />
              </Col>
            ))}
          </Row>
        </>
      )}
    </Card>
  );
}
