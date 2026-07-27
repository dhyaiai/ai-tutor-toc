import { useState, useRef, useCallback, useEffect } from "react";
import {
  Card, Tag, Typography, Space, Button, Row, Col, message,
  Radio, Checkbox, Collapse,
} from "antd";
import type { AIQuestionItem, AISubQuestionItem, AIUserAnswer } from "../services/aiQuestionService";
import { aiQuestionService } from "../services/aiQuestionService";
import type { SimilarQuestionItem } from "../services/questionService";
import SimilarQuestionCard from "./SimilarQuestionCard";
import { getScoreRate } from "../utils/helpers";

interface Props {
  item: AIQuestionItem;
}

const DIFFICULTY_MAP: Record<string, { label: string; color: string }> = {
  easy: { label: "基础", color: "green" },
  medium: { label: "中等", color: "orange" },
  hard: { label: "拔高", color: "red" },
};

const POLL_INTERVAL = 2000;
const MAX_POLL_TIME = 300000;
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
}: {
  questionType?: string | null;
  options?: Array<{ label: string; text: string }> | null;
  selectedOptions?: string[];
}) {
  const isChoice = questionType?.includes("选") || (options && options.length > 0);
  const isMulti = questionType?.includes("多选");
  const opts = options || [];
  if (!isChoice || opts.length === 0) return null;

  return (
    <div style={{ marginBottom: 8 }}>
      {isMulti ? (
        <Checkbox.Group value={selectedOptions || []} disabled>
          <Space direction="vertical" size={2}>
            {opts.map((opt) => (
              <Checkbox key={opt.label} value={opt.label}>
                <Typography.Text strong>{opt.label}.</Typography.Text> {opt.text}
              </Checkbox>
            ))}
          </Space>
        </Checkbox.Group>
      ) : (
        <Radio.Group value={selectedOptions?.[0]} disabled>
          <Space direction="vertical" size={2}>
            {opts.map((opt) => (
              <Radio key={opt.label} value={opt.label}>
                <Typography.Text strong>{opt.label}.</Typography.Text> {opt.text}
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
// 独立题卡片
// ═══════════════════════════════════════════
function StandaloneCard({ item }: { item: AIQuestionItem }) {
  const latestAnswer: AIUserAnswer | null = item.user_answers?.length
    ? item.user_answers[item.user_answers.length - 1]
    : null;

  const [similarCards, setSimilarCards] = useState<Array<SimilarQuestionItem | null> | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const aiQuestionId = item.id as number;

  const clearPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  useEffect(() => () => clearPolling(), [clearPolling]);

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

  const handleReplace = useCallback(async (index: number) => {
    try {
      const sq = await aiQuestionService.generateSimilarSingle(aiQuestionId);
      setSimilarCards((prev) => {
        const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
        updated[index] = sq;
        return updated;
      });
    } catch (e: any) {
      message.error("换题失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    }
  }, [aiQuestionId]);

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
        {latestAnswer && (
          <Tag color={latestAnswer.is_correct ? "green" : "red"}>
            {latestAnswer.is_correct ? "回答正确" : "回答错误"}
          </Tag>
        )}
      </Space>

      {/* 题目文字 */}
      <Typography.Paragraph style={{ fontSize: 13, marginBottom: 8 }}>
        {item.question_text}
      </Typography.Paragraph>

      {/* 单选题/多选题选项（只读展示） */}
      <ChildOptions
        questionType={item.question_type}
        options={item.options}
        selectedOptions={latestAnswer?.selected_options}
      />

      {/* 知识点 */}
      {item.knowledge_point && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          知识点：{item.knowledge_point}
        </Typography.Text>
      )}

      {/* 作答结果 */}
      {latestAnswer && (
        <div style={{ marginTop: 8, padding: 8, background: "#fafafa", borderRadius: 4 }}>
          <Typography.Text style={{ fontSize: 12 }}>
            得分：{latestAnswer.score}/{latestAnswer.full_score}
          </Typography.Text>
          {latestAnswer.ai_feedback && (
            <Typography.Paragraph style={{ fontSize: 12, marginBottom: 0, marginTop: 4 }} type="secondary">
              评语：{latestAnswer.ai_feedback}
            </Typography.Paragraph>
          )}
          <details style={{ marginTop: 4 }}>
            <summary style={{ cursor: "pointer", fontSize: 12, color: "#1677ff" }}>查看正确答案</summary>
            <Typography.Text style={{ fontSize: 12 }}>{item.answer}</Typography.Text>
            {item.analysis && (
              <Typography.Paragraph style={{ fontSize: 12, marginBottom: 0, marginTop: 4, whiteSpace: "pre-wrap" }}>
                <Typography.Text strong style={{ color: "#722ed1" }}>解析：</Typography.Text>
                {item.analysis}
              </Typography.Paragraph>
            )}
          </details>
        </div>
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
function BigQuestionCard({ item }: { item: AIQuestionItem }) {
  const children: AISubQuestionItem[] = item.children || [];
  const totalCount = item.total_count || children.length;
  const firstChildId = children.length > 0 ? children[0].id : 0;

  // 同类题生成（复用第一个子题的 id）
  const [similarCards, setSimilarCards] = useState<Array<SimilarQuestionItem | null> | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  useEffect(() => () => clearPolling(), [clearPolling]);

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

  const handleReplace = useCallback(async (index: number) => {
    if (!firstChildId) return;
    try {
      const sq = await aiQuestionService.generateSimilarSingle(firstChildId);
      setSimilarCards((prev) => {
        const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
        updated[index] = sq;
        return updated;
      });
    } catch (e: any) {
      message.error("换题失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    }
  }, [firstChildId]);

  const started = similarCards !== null;
  const allFilled = started && similarCards!.every((c) => c !== null);

  return (
    <Card size="small">
      {/* ── 大题背景材料 ── */}
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
          {item.question_context}
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
        {item.score_rate != null && (
          <Typography.Text style={{ fontSize: 13 }}>
            得分率：{getScoreRate(null, null, item.score_rate)}
          </Typography.Text>
        )}
      </Space>

      {/* ── 子题折叠面板 ── */}
      {children.length > 0 && (
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
                  {children.map((child, idx) => {
                    const latestAnswer: AIUserAnswer | null =
                      child.user_answers?.length
                        ? child.user_answers[child.user_answers.length - 1]
                        : null;
                    const isError =
                      latestAnswer?.is_correct === false;

                    return (
                      <Card
                        key={child.id}
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
                            {latestAnswer && (
                              isError ? (
                                <Tag color="error" style={{ fontSize: 11 }}>错误</Tag>
                              ) : (
                                <Tag color="success" style={{ fontSize: 11 }}>正确</Tag>
                              )
                            )}
                            {!latestAnswer && (
                              <Tag style={{ fontSize: 11 }}>未作答</Tag>
                            )}
                          </Space>
                        }
                      >
                        {/* 题目文字 */}
                        <Typography.Paragraph
                          style={{ marginBottom: 8, fontSize: 13, whiteSpace: "pre-wrap" }}
                        >
                          {child.question_text}
                        </Typography.Paragraph>

                        {/* 选项（只读） */}
                        <ChildOptions
                          questionType={child.question_type}
                          options={child.options}
                          selectedOptions={latestAnswer?.selected_options}
                        />

                        {/* 得分与正确答案 */}
                        {latestAnswer && (
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
                            <details style={{ marginTop: 4 }}>
                              <summary style={{ cursor: "pointer", fontSize: 12, color: "#1677ff" }}>
                                查看正确答案
                              </summary>
                              <Typography.Text style={{ fontSize: 12 }}>
                                {child.answer}
                              </Typography.Text>
                              {child.analysis && (
                                <Typography.Paragraph style={{ fontSize: 12, marginBottom: 0, marginTop: 4, whiteSpace: "pre-wrap" }}>
                                  <Typography.Text strong style={{ color: "#722ed1" }}>解析：</Typography.Text>
                                  {child.analysis}
                                </Typography.Paragraph>
                              )}
                            </details>
                          </div>
                        )}

                        {/* 知识点 */}
                        {child.knowledge_point && (
                          <div style={{ marginTop: 4 }}>
                            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                              知识点：{child.knowledge_point}
                            </Typography.Text>
                          </div>
                        )}
                      </Card>
                    );
                  })}
                </div>
              ),
            },
          ]}
        />
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
export default function AIQuestionHistoryCard({ item }: Props) {
  if (item.is_big_question) {
    return <BigQuestionCard item={item} />;
  }
  return <StandaloneCard item={item} />;
}
