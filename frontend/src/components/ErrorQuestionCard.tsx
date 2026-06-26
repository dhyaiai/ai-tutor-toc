import { useState, useRef, useCallback, useEffect } from "react";
import { Card, Tag, Button, Typography, Image, Row, Col, message } from "antd";
import { EyeOutlined, EyeInvisibleOutlined } from "@ant-design/icons";
import { questionService, type SimilarQuestionItem } from "../services/questionService";
import { getScoreRate } from "../utils/helpers";
import SimilarQuestionCard from "./SimilarQuestionCard";

interface Props {
  item: Record<string, unknown>;
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
  const [similarCards, setSimilarCards] = useState<Array<SimilarQuestionItem | null> | null>(null);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAnswer, setShowAnswer] = useState(false);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const sourceId = item.id as number;

  const clearPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => clearPolling();
  }, [clearPolling]);

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
            setError(res.error || "生成失败");
            setGenerating(false);
            // 把 null 槽位填上占位符，让用户能单独重试
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
            // 把 null 槽位填上占位符，让用户能单独重试
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

  const handleReplace = useCallback(async (index: number) => {
    try {
      const sq = await questionService.generateSimilarSingle(sourceId);
      setSimilarCards((prev) => {
        const updated = prev ? [...prev] : Array(CARD_COUNT).fill(null);
        updated[index] = sq;
        return updated;
      });
    } catch (e: any) {
      message.error("换题失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    }
  }, [sourceId]);

  // 已开始 = 已点击过按钮（不管成功失败）
  const started = similarCards !== null;
  const allFilled = started && similarCards!.every((c) => c !== null);
  const hasError = !!error;

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
