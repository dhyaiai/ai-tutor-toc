import { useState, useRef, useCallback, useEffect } from "react";
import {
  Card, Tag, Typography, Space, Button, Row, Col, message,
  Radio, Checkbox, Collapse,
} from "antd";
import { StarOutlined, StarFilled, EyeOutlined, EyeInvisibleOutlined } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import type { AIQuestionItem, AISubQuestionItem, AIUserAnswer } from "../services/aiQuestionService";
import { aiQuestionService } from "../services/aiQuestionService";
import { favoriteService, type FavoriteItemType } from "../services/favoriteService";
import type { SimilarQuestionItem } from "../services/questionService";
import MathText from "./MathText";
import { RichText } from "./MarkdownPreview";
import SimilarQuestionCard from "./SimilarQuestionCard";
import QuestionSvgImage from "./QuestionSvgImage";
import { getScoreRate, isPlaceholderAnswer } from "../utils/helpers";

interface Props {
  item: AIQuestionItem;
  /** 收藏初始状态（收藏页传入时恒为 true；缺省读列表接口回显的 is_favorited） */
  isFavorited?: boolean;
  /** 收藏状态切换成功的回调（收藏页用于从列表移除卡片） */
  onToggleFavorite?: (nowFavorited: boolean) => void;
  /** 隐藏 AI 配图（收藏页只需展示转录文字时传 true） */
  hideImage?: boolean;
  /** 隐藏学生作答痕迹（得分/评语/对错状态/选中项，收藏页只展示题目本身时传 true）。
   *  收藏页模式下同时启用：大题子题完全平铺、选项纯文字展示（无勾选框）、答案改为按钮式展示 */
  hideStudentAnswer?: boolean;
  /** 隐藏参考答案与解析（收藏页只展示题目本身时传 true） */
  hideAnswer?: boolean;
}

/**
 * 收藏状态 hook：内部调 API + 本地 state，成功后同步相关列表的 react-query 缓存。
 * 锚点 id：独立题为自身 id；大题以组内第一子题 id（firstChildId）为收藏锚点。
 */
function useFavoriteState(
  itemType: FavoriteItemType,
  anchorId: number,
  initialFav: boolean,
  onToggleFavorite?: (nowFavorited: boolean) => void,
) {
  const [fav, setFav] = useState(initialFav);
  const [favPending, setFavPending] = useState(false);
  const queryClient = useQueryClient();

  const handleToggleFavorite = useCallback(async () => {
    if (favPending || !anchorId) return;
    setFavPending(true);
    const target = !fav;
    try {
      if (target) {
        await favoriteService.add(itemType, anchorId);
      } else {
        await favoriteService.remove(itemType, anchorId);
      }
      setFav(target);
      onToggleFavorite?.(target);
      // 让繁星驱动列表页与收藏页的星标状态同步（react-query 缓存一致性）
      queryClient.invalidateQueries({ queryKey: ["aiQuestions"] });
      queryClient.invalidateQueries({ queryKey: ["favorites"] });
    } catch (e: any) {
      message.error("收藏操作失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    } finally {
      setFavPending(false);
    }
  }, [fav, favPending, itemType, anchorId, onToggleFavorite, queryClient]);

  return { fav, favPending, handleToggleFavorite };
}

/** 收藏星星按钮（实心金色 = 已收藏） */
function FavoriteButton({
  fav,
  favPending,
  onToggle,
}: {
  fav: boolean;
  favPending: boolean;
  onToggle: () => void;
}) {
  return (
    <Button
      type="text"
      size="small"
      icon={fav ? <StarFilled style={{ color: "#faad14" }} /> : <StarOutlined />}
      loading={favPending}
      onClick={onToggle}
      title={fav ? "取消收藏" : "收藏"}
      style={{ marginLeft: 8 }}
    />
  );
}

const DIFFICULTY_MAP: Record<string, { label: string; color: string }> = {
  easy: { label: "基础", color: "green" },
  medium: { label: "中等", color: "orange" },
  hard: { label: "拔高", color: "red" },
};

/**
 * 知识点字符串按常见分隔符拆分为数组（AI 题 knowledge_point 为单个字符串，
 * 生成/转录时可能塞入多个知识点），渲染为与错题一致的蓝色 Tag。
 */
function splitKnowledgePoints(kp: string): string[] {
  return kp
    .split(/[、，,;；/]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

const POLL_INTERVAL = 2000;
// 轮询总时长与后端任务时长对齐：普通题批量生成 3 题，单题超时 240s，最长 12 分钟；
// 原 5 分钟会在后端还没跑完时就提前放弃，导致结果不可达（用户看到占位卡）。
// 这里放宽到 15 分钟（后端 12 分钟 + 缓冲），轮询期间逐题更新，体验不受影响。
const MAX_POLL_TIME = 900000;
const CARD_COUNT = 3;

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

// ═══════════════════════════════════════════
// 子题选项只读渲染
// ═══════════════════════════════════════════
function ChildOptions({
  questionType,
  options,
  selectedOptions,
  plain,
}: {
  questionType?: string | null;
  options?: Array<{ label: string; text: string }> | null;
  selectedOptions?: string[];
  /** 纯文字模式（收藏页）：不渲染 Radio/Checkbox 勾选框，只展示 "A. 文本" */
  plain?: boolean;
}) {
  const isChoice = questionType?.includes("选") || (options && options.length > 0);
  const isMulti = questionType?.includes("多选");
  const opts = options || [];
  if (!isChoice || opts.length === 0) return null;

  // 收藏页模式：纯文字平铺选项，不带勾选框（只展示题目内容）
  if (plain) {
    return (
      <div style={{ marginBottom: 8 }}>
        {opts.map((opt) => (
          <div key={opt.label} style={{ marginBottom: 2 }}>
            <Typography.Text strong>{opt.label}.</Typography.Text>{" "}
            <MathText content={opt.text} />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div style={{ marginBottom: 8 }}>
      {isMulti ? (
        <Checkbox.Group value={selectedOptions || []} disabled>
          <Space direction="vertical" size={2}>
            {opts.map((opt) => (
              <Checkbox key={opt.label} value={opt.label}>
                <Typography.Text strong>{opt.label}.</Typography.Text>{" "}
                <MathText content={opt.text} />
              </Checkbox>
            ))}
          </Space>
        </Checkbox.Group>
      ) : (
        <Radio.Group value={selectedOptions?.[0]} disabled>
          <Space direction="vertical" size={2}>
            {opts.map((opt) => (
              <Radio key={opt.label} value={opt.label}>
                <Typography.Text strong>{opt.label}.</Typography.Text>{" "}
                <MathText content={opt.text} />
              </Radio>
            ))}
          </Space>
        </Radio.Group>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════
// 子题题型标签
// ═══════════════════════════════════════════
function getChildTypeTag(questionType?: string | null) {
  if (!questionType) return null;
  if (questionType.includes("多选")) return <Tag color="red" style={{ fontSize: 11 }}>多选题</Tag>;
  if (questionType.includes("选")) return <Tag color="blue" style={{ fontSize: 11 }}>单选题</Tag>;
  return <Tag color="purple" style={{ fontSize: 11 }}>{questionType}</Tag>;
}

// ═══════════════════════════════════════════
// 大题子题卡片（独立子组件以持有自己的 showAnswer 状态）
// ═══════════════════════════════════════════
function AISubQuestionCard({
  child,
  hideStudentAnswer,
  hideAnswer,
  hideImage,
}: {
  child: AISubQuestionItem;
  hideStudentAnswer?: boolean;
  hideAnswer?: boolean;
  hideImage?: boolean;
}) {
  const [showAnswer, setShowAnswer] = useState(false);
  const latestAnswer: AIUserAnswer | null =
    child.user_answers?.length
      ? child.user_answers[child.user_answers.length - 1]
      : null;
  // 隐藏学生作答时不算对错（无作答数据可依赖）
  const isError =
    !hideStudentAnswer && latestAnswer?.is_correct === false;

  /* ── 收藏页模式：紧凑块排版（一个大题切块内的小题块） ── */
  if (hideStudentAnswer) {
    return (
      <div style={{ padding: "10px 0", borderBottom: "1px dashed #f0f0f0" }}>
        {/* 题号行 + 查看答案按钮 */}
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
            {getChildTypeTag(child.question_type)}
          </Space>
          {!hideAnswer && (child.answer || child.analysis) && (
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
        {/* 题目文字 */}
        <RichText
          content={child.question_text}
          style={{ display: "block", marginBottom: 8, fontSize: 13 }}
        />
        {/* AI 生成的子题配图（SVG）；hideImage 时（收藏页）不展示 */}
        {!hideImage && child.image_svg && <QuestionSvgImage svg={child.image_svg} />}
        {/* 选项：纯文字模式（无勾选框） */}
        <ChildOptions
          questionType={child.question_type}
          options={child.options}
          plain
        />
        {/* 答案展开区：正确答案 + 解析（AI 题有完整解析则显示，缺失显示"无"） */}
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
            {child.answer && !isPlaceholderAnswer(child.answer) && (
              <div>
                <Typography.Text strong style={{ fontSize: 13, color: "#52c41a" }}>
                  正确答案：
                </Typography.Text>
                <RichText content={child.answer} style={{ fontSize: 13 }} />
              </div>
            )}
            <div style={{ marginTop: 8 }}>
              <Typography.Text strong style={{ fontSize: 13, color: "#722ed1" }}>
                解析：
              </Typography.Text>
              {child.analysis ? (
                <RichText content={child.analysis} style={{ display: "block", fontSize: 13, marginTop: 4 }} />
              ) : (
                <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                  无
                </Typography.Text>
              )}
            </div>
          </div>
        )}
        {/* 知识点 */}
        {child.knowledge_point && (
          <div style={{ marginTop: 4 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              知识点：
            </Typography.Text>
            {splitKnowledgePoints(child.knowledge_point).map((name) => (
              <Tag key={name} color="blue" style={{ fontSize: 10, marginTop: 2, fontWeight: 600 }}>
                <MathText content={name} />
              </Tag>
            ))}
          </div>
        )}
      </div>
    );
  }

  /* ── 其他页面（AI 挑战）：保持原逻辑（独立 Card） ── */
  return (
    <Card
      size="small"
      style={{
        marginBottom: 8,
        borderLeft: latestAnswer
          ? isError
            ? "3px solid #ff4d4f"
            : "3px solid #52c41a"
          : "3px solid #d9d9d9",
      }}
      title={
        <Space size={4}>
          <Typography.Text style={{ fontSize: 13 }}>
            小题 {child.sub_question_index + 1}
          </Typography.Text>
          {getChildTypeTag(child.question_type)}
          {latestAnswer ? (
            isError ? (
              <Tag color="error" style={{ fontSize: 11 }}>错误</Tag>
            ) : (
              <Tag color="success" style={{ fontSize: 11 }}>正确</Tag>
            )
          ) : (
            <Tag style={{ fontSize: 11 }}>未作答</Tag>
          )}
        </Space>
      }
    >
      {/* 题目文字 */}
      <MathText
        content={child.question_text}
        style={{ display: "block", marginBottom: 8, fontSize: 13 }}
      />

      {/* AI 生成的子题配图（SVG）；hideImage 时（收藏页）不展示 */}
      {!hideImage && child.image_svg && <QuestionSvgImage svg={child.image_svg} />}

      {/* 选项（只读勾选框 + 选中态回显） */}
      <ChildOptions
        questionType={child.question_type}
        options={child.options}
        selectedOptions={latestAnswer?.selected_options}
      />

      {latestAnswer && (
        /* 作答结果：得分 / 评语 / 我的作答（有作答记录时显示） */
        <div
          style={{
            marginTop: 8,
            padding: 8,
            background: isError ? "#fff2f0" : "#f6ffed",
            borderRadius: 4,
            border: `1px solid ${isError ? "#ffccc7" : "#b7eb8f"}`,
          }}
        >
          <Typography.Text style={{ fontSize: 12 }}>
            得分：{latestAnswer.score}/{latestAnswer.full_score}
          </Typography.Text>
          {latestAnswer.ai_feedback && (
            <Typography.Paragraph
              style={{ fontSize: 12, marginBottom: 0, marginTop: 4 }}
              type="secondary"
            >
              评语：{latestAnswer.ai_feedback}
            </Typography.Paragraph>
          )}
          {/* 主观题作答内容（answer_text 只存主观题；选择题由选项勾选态回显） */}
          {latestAnswer.answer_text && (
            <Typography.Paragraph style={{ fontSize: 12, marginBottom: 0, marginTop: 4, whiteSpace: "pre-wrap" }}>
              <Typography.Text strong>我的作答：</Typography.Text>
              {latestAnswer.answer_text}
            </Typography.Paragraph>
          )}
          {latestAnswer.answer_image_url && (
            <div style={{ marginTop: 4 }}>
              <Typography.Text strong style={{ fontSize: 12 }}>我的作答图片：</Typography.Text>
              <img
                src={latestAnswer.answer_image_url}
                alt="我的作答图片"
                style={{ display: "block", maxWidth: "100%", marginTop: 4, borderRadius: 4 }}
              />
            </div>
          )}
        </div>
      )}
      {/* 查看正确答案：题有答案/解析即可查看（未作答也能看），不依赖作答记录 */}
      {!hideAnswer && (child.answer || child.analysis) && (
        <div style={{ marginTop: 8 }}>
          <details>
            <summary style={{ cursor: "pointer", fontSize: 12, color: "#1677ff" }}>
              查看正确答案
            </summary>
            {child.answer && !isPlaceholderAnswer(child.answer) && (
              <RichText content={child.answer} style={{ fontSize: 12 }} />
            )}
            {child.analysis && (
              <div style={{ marginTop: 4 }}>
                <Typography.Text strong style={{ color: "#722ed1", fontSize: 12 }}>解析：</Typography.Text>
                <RichText content={child.analysis} style={{ display: "block", fontSize: 12, marginTop: 4 }} />
              </div>
            )}
          </details>
        </div>
      )}

      {/* 知识点 */}
      {child.knowledge_point && (
        <div style={{ marginTop: 4 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            知识点：
          </Typography.Text>
          {splitKnowledgePoints(child.knowledge_point).map((name) => (
            <Tag key={name} color="blue" style={{ fontSize: 10, marginTop: 2, fontWeight: 600 }}>
              <MathText content={name} />
            </Tag>
          ))}
        </div>
      )}
    </Card>
  );
}

// ═══════════════════════════════════════════
// 独立题卡片
// ═══════════════════════════════════════════
function StandaloneCard({
  item,
  isFavorited,
  onToggleFavorite,
  hideImage,
  hideStudentAnswer,
  hideAnswer,
}: {
  item: AIQuestionItem;
  isFavorited?: boolean;
  onToggleFavorite?: (nowFavorited: boolean) => void;
  hideImage?: boolean;
  hideStudentAnswer?: boolean;
  hideAnswer?: boolean;
}) {
  const latestAnswer: AIUserAnswer | null = item.user_answers?.length
    ? item.user_answers[item.user_answers.length - 1]
    : null;

  // 收藏状态（锚点 = 独立题自身 id）
  const { fav, favPending, handleToggleFavorite } = useFavoriteState(
    "ai",
    item.id ?? 0,
    isFavorited ?? item.is_favorited ?? false,
    onToggleFavorite,
  );

  const [similarCards, setSimilarCards] = useState<Array<SimilarQuestionItem | null> | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  // 收藏页模式下"查看答案"按钮展开状态
  const [showAnswer, setShowAnswer] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 换一题（replace）轮询独立 ref：与批量生成轮询互不干扰，
  // 避免换题时把仍在进行中的批量轮询清掉导致卡片停在占位态
  const replacePollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const aiQuestionId = item.id as number;

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

  useEffect(() => () => {
    clearPolling();
    clearReplacePolling();
  }, [clearPolling, clearReplacePolling]);

  const handleGenerate = useCallback(async () => {
    setGenerating(true);
    setSimilarCards(Array(CARD_COUNT).fill(null));
    setGenError(null);
    clearPolling();

    try {
      await aiQuestionService.generateSimilar(aiQuestionId);
      const startTime = Date.now();

      pollingRef.current = setInterval(async () => {
        try {
          const res = await aiQuestionService.getSimilarResult(aiQuestionId);
          const questions = res.similar_questions || [];

          if (questions.length > 0) {
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
            setGenError(res.error || "生成失败");
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
            setGenError("生成超时，请稍后重试");
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
      setGenError(`创建任务失败: ${detail}`);
      setGenerating(false);
    }
  }, [aiQuestionId, clearPolling]);

  /** 换一题（异步任务 + 轮询 replace 结果）。
   * 原实现同步等待 LLM（后端最长 360s，前端 axios 120s 必超时），表现为"换一题没反应"。
   * 现在：先置卡片为加载态 → 创建后台任务（202 快速返回）→ 轮询 replace 字段替换卡片；
   * 失败/超时恢复原题。 */
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
      await aiQuestionService.generateSimilarSingle(aiQuestionId, "medium", index);
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
        const res = await aiQuestionService.getSimilarResult(aiQuestionId);
        const rep = res.replace;
        if (rep && rep.status === "completed") {
          clearReplacePolling();
          if (rep.question) {
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
  }, [aiQuestionId, similarCards, clearReplacePolling]);

  const started = similarCards !== null;
  const allFilled = started && similarCards!.every((c) => c !== null);

  return (
    <Card size="small">
      {/* 题型 / 难度 / 对错标签 */}
      <Space style={{ marginBottom: 8 }}>
        <Tag color="purple">{item.question_type || "未知题型"}</Tag>
        {item.difficulty && DIFFICULTY_MAP[item.difficulty] && (
          <Tag color={DIFFICULTY_MAP[item.difficulty].color}>
            {DIFFICULTY_MAP[item.difficulty].label}
          </Tag>
        )}
        {!hideStudentAnswer && latestAnswer && (
          <Tag color={latestAnswer.is_correct ? "green" : "red"}>
            {latestAnswer.is_correct ? "回答正确" : "回答错误"}
          </Tag>
        )}
        <FavoriteButton fav={fav} favPending={favPending} onToggle={handleToggleFavorite} />
      </Space>

      {/* 题目文字 */}
      <RichText
        content={item.question_text}
        style={{ display: "block", fontSize: 13, marginBottom: 8 }}
      />

      {/* AI 生成的题目配图（SVG）；hideImage 时（收藏页）不展示 */}
      {!hideImage && item.image_svg && <QuestionSvgImage svg={item.image_svg} />}

      {/* 单选题/多选题选项（收藏页纯文字模式无勾选框；其他页面只读勾选框 + 选中态回显） */}
      <ChildOptions
        questionType={item.question_type}
        options={item.options}
        selectedOptions={hideStudentAnswer ? undefined : latestAnswer?.selected_options}
        plain={hideStudentAnswer}
      />

      {/* 知识点 */}
      {item.knowledge_point && (
        <div style={{ marginTop: 4 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>
            知识点：
          </Typography.Text>
          {splitKnowledgePoints(item.knowledge_point).map((name) => (
            <Tag key={name} color="blue" style={{ fontSize: 10, marginTop: 2, fontWeight: 600 }}>
              <MathText content={name} />
            </Tag>
          ))}
        </div>
      )}

      {/* 参考答案与解析 */}
      {hideStudentAnswer ? (
        /* 收藏页模式：按钮式展示正确答案 + 解析（不展示学生作答） */
        !hideAnswer && (item.answer || item.analysis) && (
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
                {item.answer && !isPlaceholderAnswer(item.answer) && (
                  <div>
                    <Typography.Text strong style={{ fontSize: 13, color: "#52c41a" }}>
                      正确答案：
                    </Typography.Text>
                    <RichText content={item.answer} style={{ fontSize: 13 }} />
                  </div>
                )}
                <div style={{ marginTop: 8 }}>
                  <Typography.Text strong style={{ fontSize: 13, color: "#722ed1" }}>
                    解析：
                  </Typography.Text>
                  {item.analysis ? (
                    <RichText content={item.analysis} style={{ display: "block", fontSize: 13, marginTop: 4 }} />
                  ) : (
                    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                      无
                    </Typography.Text>
                  )}
                </div>
              </div>
            )}
          </div>
        )
      ) : (
        <>
          {latestAnswer && (
            /* 作答结果：得分 / 得分率 / 评语 / 我的作答（有作答记录时显示） */
            <div style={{ marginTop: 8, padding: 8, background: "#fafafa", borderRadius: 4 }}>
              <Typography.Text style={{ fontSize: 12 }}>
                得分：{latestAnswer.score}/{latestAnswer.full_score}
                {latestAnswer.score != null && latestAnswer.full_score != null && (
                  <span style={{ marginLeft: 8, color: "#666" }}>
                    得分率：{getScoreRate(latestAnswer.score, latestAnswer.full_score)}
                  </span>
                )}
              </Typography.Text>
              {latestAnswer.ai_feedback && (
                <Typography.Paragraph style={{ fontSize: 12, marginBottom: 0, marginTop: 4 }} type="secondary">
                  评语：{latestAnswer.ai_feedback}
                </Typography.Paragraph>
              )}
              {/* 主观题作答内容（answer_text 只存主观题；选择题由选项勾选态回显） */}
              {latestAnswer.answer_text && (
                <Typography.Paragraph style={{ fontSize: 12, marginBottom: 0, marginTop: 4, whiteSpace: "pre-wrap" }}>
                  <Typography.Text strong>我的作答：</Typography.Text>
                  {latestAnswer.answer_text}
                </Typography.Paragraph>
              )}
              {latestAnswer.answer_image_url && (
                <div style={{ marginTop: 4 }}>
                  <Typography.Text strong style={{ fontSize: 12 }}>我的作答图片：</Typography.Text>
                  <img
                    src={latestAnswer.answer_image_url}
                    alt="我的作答图片"
                    style={{ display: "block", maxWidth: "100%", marginTop: 4, borderRadius: 4 }}
                  />
                </div>
              )}
            </div>
          )}
          {/* 查看正确答案：题有答案/解析即可查看（未作答也能看），不依赖作答记录 */}
          {!hideAnswer && (item.answer || item.analysis) && (
            <div style={{ marginTop: 8 }}>
              <details>
                <summary style={{ cursor: "pointer", fontSize: 12, color: "#1677ff" }}>查看正确答案</summary>
                {item.answer && !isPlaceholderAnswer(item.answer) && (
                  <RichText content={item.answer} style={{ fontSize: 12 }} />
                )}
                {item.analysis && (
                  <div style={{ marginTop: 4 }}>
                    <Typography.Text strong style={{ color: "#722ed1", fontSize: 12 }}>解析：</Typography.Text>
                    <RichText content={item.analysis} style={{ display: "block", fontSize: 12, marginTop: 4 }} />
                  </div>
                )}
              </details>
            </div>
          )}
        </>
      )}

      {/* AI 生成同类题按钮 */}
      <div style={{ marginTop: 8 }}>
        <Button
          type={generating ? "default" : "primary"}
          size="small"
          loading={generating}
          onClick={handleGenerate}
          disabled={generating}
          style={generating ? { color: "#52c41a", borderColor: "#52c41a" } : undefined}
        >
          <span style={generating ? { color: "#52c41a" } : undefined}>
            {allFilled ? "重新生成" : "AI 生成同类题"}
          </span>
        </Button>
      </div>

      {/* 同类题卡片区域 */}
      {started && (
        <>
          {genError && (
            <Typography.Text type="danger" style={{ display: "block", marginTop: 12, fontSize: 13 }}>
              {genError}
            </Typography.Text>
          )}
          <Row gutter={[12, 12]} style={{ marginTop: genError ? 8 : 16 }}>
            {similarCards!.map((q, i) => (
              <Col key={i} xs={24} sm={12} md={8}>
                <SimilarQuestionCard
                  index={i}
                  question={q}
                  questionId={item.source_question_id || 0}
                  onReplace={handleReplace}
                />
              </Col>
            ))}
          </Row>
        </>
      )}

      {/* 创建时间 */}
      <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 8 }}>
        {item.created_at ? new Date(item.created_at).toLocaleString("zh-CN") : ""}
      </Typography.Text>
    </Card>
  );
}

// ═══════════════════════════════════════════
// 大题卡片（参照 ErrorQuestionCard 大题模式）
// ═══════════════════════════════════════════
function BigQuestionCard({
  item,
  isFavorited,
  onToggleFavorite,
  hideImage,
  hideStudentAnswer,
  hideAnswer,
}: {
  item: AIQuestionItem;
  isFavorited?: boolean;
  onToggleFavorite?: (nowFavorited: boolean) => void;
  hideImage?: boolean;
  hideStudentAnswer?: boolean;
  hideAnswer?: boolean;
}) {
  const children: AISubQuestionItem[] = item.children || [];
  const totalCount = item.total_count || children.length;
  const firstChildId = children.length > 0 ? children[0].id : 0;

  // 收藏状态（大题以组内第一子题 id 为锚点；children 为空时不展示收藏按钮）
  const { fav, favPending, handleToggleFavorite } = useFavoriteState(
    "ai",
    firstChildId,
    isFavorited ?? item.is_favorited ?? false,
    onToggleFavorite,
  );

  // 同类题生成（复用第一个子题的 id）
  const [similarCards, setSimilarCards] = useState<Array<SimilarQuestionItem | null> | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 换一题（replace）轮询独立 ref：与批量生成轮询互不干扰，
  // 避免换题时把仍在进行中的批量轮询清掉导致卡片停在占位态
  const replacePollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

  useEffect(() => () => {
    clearPolling();
    clearReplacePolling();
  }, [clearPolling, clearReplacePolling]);

  const handleGenerate = useCallback(async () => {
    if (!firstChildId) return;
    setGenerating(true);
    setSimilarCards(Array(CARD_COUNT).fill(null));
    setGenError(null);
    clearPolling();

    try {
      await aiQuestionService.generateSimilar(firstChildId);
      const startTime = Date.now();

      pollingRef.current = setInterval(async () => {
        try {
          const res = await aiQuestionService.getSimilarResult(firstChildId);
          const questions = res.similar_questions || [];

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
            setGenError(res.error || "生成失败");
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
            setGenError("生成超时，请稍后重试");
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
      setGenError(`创建任务失败: ${detail}`);
      setGenerating(false);
    }
  }, [firstChildId, clearPolling]);

  /** 换一题（异步任务 + 轮询 replace 结果，逻辑同 StandaloneCard） */
  const handleReplace = useCallback(async (index: number) => {
    if (!firstChildId) return;
    // 记住原题，失败/超时时恢复
    const oldQ = similarCards?.[index] ?? null;
    clearReplacePolling();
    setSimilarCards((prev) => {
      const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
      updated[index] = null; // 卡片显示 Skeleton 加载态
      return updated;
    });

    try {
      await aiQuestionService.generateSimilarSingle(firstChildId, "medium", index);
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
        const res = await aiQuestionService.getSimilarResult(firstChildId);
        const rep = res.replace;
        if (rep && rep.status === "completed") {
          clearReplacePolling();
          if (rep.question) {
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
  }, [firstChildId, similarCards, clearReplacePolling]);

  const started = similarCards !== null;
  const allFilled = started && similarCards!.every((c) => c !== null);

  // 统一查看答案展开状态（收藏页模式，一次性展开所有子题答案）
  const [showAllAnswers, setShowAllAnswers] = useState(false);

  return (
    <Card size="small">
      {/* ── 大题背景材料（题干） ── */}
      {item.question_context && (
        <div
          style={{
            padding: 12,
            background: "#fafafa",
            borderRadius: 6,
            border: "1px solid #e8e8e8",
            marginBottom: 12,
            whiteSpace: "pre-wrap",
            fontSize: 13,
            lineHeight: 1.8,
          }}
        >
          <Typography.Text strong style={{ fontSize: 13, display: "block", marginBottom: 4 }}>
            📖 阅读材料
          </Typography.Text>
          <RichText content={item.question_context} style={{ fontSize: 13 }} />
          {/* AI 生成的大题背景配图（SVG） */}
          {!hideImage && item.context_image_svg && <QuestionSvgImage svg={item.context_image_svg} />}
        </div>
      )}

      {/* ── 大题标签行 ── */}
      <Space style={{ marginBottom: 8 }} wrap>
        <Tag color="purple">大题</Tag>
        <Tag color="orange">共 {totalCount} 小题</Tag>
        {item.difficulty && DIFFICULTY_MAP[item.difficulty] && (
          <Tag color={DIFFICULTY_MAP[item.difficulty].color}>
            {DIFFICULTY_MAP[item.difficulty].label}
          </Tag>
        )}
        {!hideStudentAnswer && item.score_rate != null && (
          <Typography.Text style={{ fontSize: 13 }}>
            得分率：{getScoreRate(null, null, item.score_rate)}
          </Typography.Text>
        )}
        {children.length > 0 && (
          <FavoriteButton fav={fav} favPending={favPending} onToggle={handleToggleFavorite} />
        )}
      </Space>

      {/* ── 子题列表：收藏页模式（hideStudentAnswer）整体展示——
          小题换行排列、一个"查看答案"按钮统一展开全部；其他页面保持折叠面板 ── */}
      {children.length > 0 && (
        hideStudentAnswer ? (
          <div style={{ marginBottom: 12 }}>
            {/* 各小题：换行展示，不再使用独立卡片切块 */}
            {children.map((child, idx) => (
              <div key={child.id} style={{ marginBottom: 8, lineHeight: 1.8 }}>
                <Space size={4} align="start">
                  <Typography.Text strong style={{ fontSize: 13 }}>
                    ({idx + 1})
                  </Typography.Text>
                  {/* 题型标签只打在大题整体，子题行不再单独打标签 */}
                  <RichText content={child.question_text} style={{ fontSize: 13 }} />
                </Space>
                {/* 选项：纯文字模式 */}
                <ChildOptions
                  questionType={child.question_type}
                  options={child.options}
                  plain
                />
                {/* 子题配图 */}
                {!hideImage && child.image_svg && (
                  <QuestionSvgImage svg={child.image_svg} />
                )}
              </div>
            ))}
            {/* 统一"查看答案"按钮：一次性展开/折叠所有子题的正确答案与解析 */}
            {children.some((c) => c.answer || c.analysis) && (
              <div style={{ marginTop: 8 }}>
                <Button
                  type="link"
                  size="small"
                  icon={showAllAnswers ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                  onClick={() => setShowAllAnswers(!showAllAnswers)}
                  style={{ padding: "0 4px", fontSize: 13 }}
                >
                  {showAllAnswers ? "折叠答案" : "查看答案"}
                </Button>
                {showAllAnswers && (
                  <div
                    style={{
                      marginTop: 8,
                      padding: 12,
                      background: "#fafafa",
                      borderRadius: 6,
                      border: "1px solid #e8e8e8",
                    }}
                  >
                    {children.map((child, idx) => (
                      <div
                        key={child.id}
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
                        {child.answer && !isPlaceholderAnswer(child.answer) && (
                          <>
                            <Typography.Text strong style={{ fontSize: 13, color: "#52c41a" }}>
                              正确答案：
                            </Typography.Text>
                            <RichText content={child.answer} style={{ fontSize: 13 }} />
                          </>
                        )}
                        <div style={{ marginTop: 4 }}>
                          <Typography.Text strong style={{ fontSize: 13, color: "#722ed1" }}>
                            解析：
                          </Typography.Text>
                          {child.analysis ? (
                            <MathText
                              content={child.analysis}
                              style={{ display: "block", fontSize: 13, marginTop: 2 }}
                            />
                          ) : (
                            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                              无
                            </Typography.Text>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            {/* 知识点汇总（去重后统一展示） */}
            {(() => {
              const kpSet = new Set<string>();
              children.forEach((child) => {
                if (child.knowledge_point) {
                  splitKnowledgePoints(child.knowledge_point).forEach((n) => kpSet.add(n));
                }
              });
              if (kpSet.size === 0) return null;
              return (
                <div style={{ marginTop: 8 }}>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    知识点：
                  </Typography.Text>
                  {Array.from(kpSet).map((name) => (
                    <Tag key={name} color="blue" style={{ fontSize: 10, marginTop: 2, fontWeight: 600 }}>
                      <MathText content={name} />
                    </Tag>
                  ))}
                </div>
              );
            })()}
          </div>
        ) : (
          <Collapse
            style={{ marginBottom: 12 }}
            items={[
              {
                key: "sub-questions",
                label: (
                  <Typography.Text strong style={{ fontSize: 13 }}>
                    小题详情（{children.length} 题）
                  </Typography.Text>
                ),
                children: (
                  <div>
                    {children.map((child) => (
                      <AISubQuestionCard
                        key={child.id}
                        child={child}
                        hideImage={hideImage}
                      />
                    ))}
                  </div>
                ),
              },
            ]}
          />
        )
      )}

      {/* ── AI 生成同类题按钮 ── */}
      <div style={{ marginBottom: 8 }}>
        <Button
          type={generating ? "default" : "primary"}
          size="small"
          loading={generating}
          onClick={handleGenerate}
          disabled={generating}
          style={generating ? { color: "#52c41a", borderColor: "#52c41a" } : undefined}
        >
          <span style={generating ? { color: "#52c41a" } : undefined}>
            {allFilled ? "重新生成" : "AI 生成同类题"}
          </span>
        </Button>
      </div>

      {/* ── 同类题卡片区域 ── */}
      {started && (
        <>
          {genError && (
            <Typography.Text type="danger" style={{ display: "block", marginTop: 12, fontSize: 13 }}>
              {genError}
            </Typography.Text>
          )}
          <Row gutter={[12, 12]} style={{ marginTop: genError ? 8 : 16 }}>
            {similarCards!.map((q, i) => (
              <Col key={i} xs={24} sm={12} md={8}>
                <SimilarQuestionCard
                  index={i}
                  question={q}
                  questionId={item.source_question_id || 0}
                  onReplace={handleReplace}
                />
              </Col>
            ))}
          </Row>
        </>
      )}

      {/* 创建时间 */}
      <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 8 }}>
        {item.created_at ? new Date(item.created_at).toLocaleString("zh-CN") : ""}
      </Typography.Text>
    </Card>
  );
}

// ═══════════════════════════════════════════
// 主组件：按 is_big_question 分流
// ═══════════════════════════════════════════
export default function AIQuestionHistoryCard({ item, isFavorited, onToggleFavorite, hideImage, hideStudentAnswer, hideAnswer }: Props) {
  if (item.is_big_question) {
    return (
      <BigQuestionCard
        item={item}
        isFavorited={isFavorited}
        onToggleFavorite={onToggleFavorite}
        hideImage={hideImage}
        hideStudentAnswer={hideStudentAnswer}
        hideAnswer={hideAnswer}
      />
    );
  }
  return (
    <StandaloneCard
      item={item}
      isFavorited={isFavorited}
      onToggleFavorite={onToggleFavorite}
      hideImage={hideImage}
      hideStudentAnswer={hideStudentAnswer}
      hideAnswer={hideAnswer}
    />
  );
}
