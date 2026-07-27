import { useState, useRef, useCallback, useEffect } from "react";
import {
  Card, Tag, Button, Typography, Image, Row, Col, Space, message, Collapse,
} from "antd";
import { EyeOutlined, EyeInvisibleOutlined } from "@ant-design/icons";
import { questionService, type SimilarQuestionItem, type SimilarBigQuestion } from "../services/questionService";
import type { ErrorQuestionItem, SubQuestionItem } from "../services/errorQuestionService";
import { getScoreRate } from "../utils/helpers";
import SimilarQuestionCard from "./SimilarQuestionCard";
import SimilarBigQuestionCard from "./SimilarBigQuestionCard";

interface Props {
  item: ErrorQuestionItem;
}

const POLL_INTERVAL = 2000;
const MAX_POLL_TIME = 300000;
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

export default function ErrorQuestionCard({ item }: Props) {
  // 小题目（普通题）状态
  const [similarCards, setSimilarCards] = useState<Array<SimilarQuestionItem | null> | null>(null);
  // 大题目（大题）状态
  const [similarBigQuestion, setSimilarBigQuestion] = useState<SimilarBigQuestion | null>(null);
  const [bigGenerating, setBigGenerating] = useState(false);

  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAnswer, setShowAnswer] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sourceId = item.id as number;
  const isBigQuestion = (item.is_big_question as boolean) || false;
  const children: SubQuestionItem[] = (item.children as SubQuestionItem[]) || [];

  const clearPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => clearPolling();
  }, [clearPolling]);

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

  /** 普通题：换一题 */
  const handleReplace = useCallback(async (index: number) => {
    try {
      const sq = await questionService.generateSimilarSingle(sourceId);
      if (!("is_big_question" in sq)) {
        setSimilarCards((prev) => {
          const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
          updated[index] = sq as SimilarQuestionItem;
          return updated;
        });
      }
    } catch (e: any) {
      message.error("换题失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    }
  }, [sourceId]);

  /** 大题：换一题（指定难度） */
  const handleReplaceBig = useCallback(async (difficulty: string) => {
    try {
      const res = await questionService.generateSimilarSingle(sourceId, difficulty);
      if ("is_big_question" in res && res.is_big_question) {
        setSimilarBigQuestion(res as SimilarBigQuestion);
      }
    } catch (e: any) {
      message.error("换题失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    }
  }, [sourceId]);

  // ── 已开始 = 已点击过按钮（不管成功失败）──
  const started = similarCards !== null || similarBigQuestion !== null;
  const hasError = !!error;

  /** 判断子题类型 */
  const getChildTypeTag = (child: SubQuestionItem) => {
    const qt = child.question_type;
    if (!qt) return null;
    if (qt.includes("多选")) return <Tag color="red" style={{ fontSize: 11 }}>多选题</Tag>;
    if (qt.includes("选")) return <Tag color="blue" style={{ fontSize: 11 }}>单选题</Tag>;
    return <Tag color="purple" style={{ fontSize: 11 }}>{qt}</Tag>;
  };

  // ═══════════════════════════════════════════
  // 大题渲染模式
  // ═══════════════════════════════════════════
  if (isBigQuestion) {
    const errorCount = (item.error_count as number) || 0;
    const totalCount = (item.total_count as number) || children.length;

    return (
      <Card size="small">
        <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
          {item.image_url && (
            <Image
              src={item.image_url as string}
              alt={`第${item.question_number}大题`}
              width={160}
              style={{ borderRadius: 4 }}
            />
          )}
          <div style={{ flex: 1 }}>
            <Typography.Text strong>
              第 {item.question_number as number} 大题 — {item.assignment_name as string}
            </Typography.Text>
            {item.question_type && (
              <Tag color="purple" style={{ marginLeft: 8 }}>{item.question_type as string}</Tag>
            )}
            <Tag color="orange" style={{ marginLeft: 4 }}>
              共 {totalCount} 小题，错 {errorCount} 题
            </Tag>
            <div style={{ marginTop: 8 }}>
              <Typography.Text>
                得分率：{getScoreRate(null, null, item.score_rate as number | undefined)}
              </Typography.Text>
            </div>

            {item.knowledge_points && (
              <div style={{ marginTop: 4 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>知识点：</Typography.Text>
                {(Array.isArray(item.knowledge_points)
                  ? (item.knowledge_points as Array<string | { name: string }>)
                  : []
                ).map((kp, i: number) => {
                  const name = typeof kp === "string" ? kp : kp.name;
                  return (
                    <Tag key={`${name}-${i}`} color="blue" style={{ marginTop: 2 }}>
                      {name}
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

        {/* 子题列表（折叠面板） */}
        {children.length > 0 && (
          <Collapse
            style={{ marginTop: 12 }}
            items={[
              {
                key: "sub-questions",
                label: <Typography.Text strong style={{ fontSize: 13 }}>小题详情（{children.length} 题）</Typography.Text>,
                children: (
                  <div>
                    {children.map((child: SubQuestionItem, idx: number) => {
                      const subIndex = (child.sub_question_index as number) ?? idx;
                      const isError =
                        child.score != null &&
                        child.full_score != null &&
                        (child.score as number) < (child.full_score as number);
                      return (
                        <Card
                          key={child.id as number}
                          size="small"
                          style={{
                            marginBottom: 8,
                            borderLeft: isError ? "3px solid #ff4d4f" : "3px solid #52c41a",
                          }}
                          title={
                            <Space size={4}>
                              <Typography.Text style={{ fontSize: 13 }}>
                                小题 {subIndex + 1}
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
                                <Typography.Text style={{ fontSize: 12 }}>
                                  {child.student_answer as string}
                                </Typography.Text>
                              </div>
                            )}
                            {child.correct_answer && (
                              <div>
                                <Typography.Text type="secondary" style={{ fontSize: 12, color: "#52c41a" }}>
                                  正确答案：
                                </Typography.Text>
                                <Typography.Text style={{ fontSize: 12 }}>
                                  {child.correct_answer as string}
                                </Typography.Text>
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
                                  <Tag key={`${name}-${i}`} color="blue" style={{ fontSize: 10, marginTop: 2 }}>
                                    {name}
                                  </Tag>
                                );
                              })}
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
        {item.image_url && (
          <Image
            src={item.image_url as string}
            alt={`第${item.question_number}题`}
            width={160}
            style={{ borderRadius: 4 }}
          />
        )}
        <div style={{ flex: 1 }}>
          <Typography.Text strong>
            第 {item.question_number as number} 题 — {item.assignment_name as string}
          </Typography.Text>
          {item.question_type && (
            <Tag color="purple" style={{ marginLeft: 8 }}>{item.question_type as string}</Tag>
          )}
          <div style={{ marginTop: 8 }}>
            <Typography.Text>
              得分率：{getScoreRate(item.score as number | null, item.full_score as number | null)}
            </Typography.Text>
          </div>
          {/* 原题答案折叠区域 */}
          {(item.student_answer || item.correct_answer) && (
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
                  {item.student_answer && (
                    <div style={{ marginBottom: item.correct_answer ? 10 : 0 }}>
                      <Typography.Text strong style={{ fontSize: 13, color: "#ff4d4f" }}>
                        我的答案：
                      </Typography.Text>
                      <Typography.Text style={{ fontSize: 13 }}>
                        {item.student_answer as string}
                      </Typography.Text>
                    </div>
                  )}
                  {item.correct_answer && (
                    <div>
                      <Typography.Text strong style={{ fontSize: 13, color: "#52c41a" }}>
                        正确答案：
                      </Typography.Text>
                      <Typography.Text style={{ fontSize: 13 }}>
                        {item.correct_answer as string}
                      </Typography.Text>
                    </div>
                  )}
                  {item.score != null && item.full_score != null && (
                    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #d9d9d9" }}>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        得分：{item.score as number}/{item.full_score as number}
                      </Typography.Text>
                      {item.analysis_detail && (
                        <Typography.Paragraph
                          style={{ marginTop: 4, fontSize: 12, marginBottom: 0 }}
                          type="secondary"
                        >
                          分析：{item.analysis_detail as string}
                        </Typography.Paragraph>
                      )}
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
                  <Tag key={`${name}-${i}`} color="blue" style={{ marginTop: 2 }}>
                    {name}
                  </Tag>
                );
              })}
            </div>
          )}
          {item.common_mistakes && Array.isArray(item.common_mistakes) && (item.common_mistakes as string[]).length > 0 && (
            <div style={{ marginTop: 4 }}>
              <Typography.Text type="warning" style={{ fontSize: 12 }}>常见错误：</Typography.Text>
              {(item.common_mistakes as string[]).map((m: string, i: number) => (
                <Tag key={i} color="orange" style={{ marginTop: 2 }}>{m}</Tag>
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
