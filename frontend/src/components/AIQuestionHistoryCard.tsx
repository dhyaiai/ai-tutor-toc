import { useState, useRef, useCallback, useEffect } from "react";
import { Card, Tag, Typography, Space, Button, Row, Col, message } from "antd";
import type { AIQuestionItem } from "../services/aiQuestionService";
import { aiQuestionService } from "../services/aiQuestionService";
import type { SimilarQuestionItem } from "../services/questionService";
import SimilarQuestionCard from "./SimilarQuestionCard";

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

export default function AIQuestionHistoryCard({ item }: Props) {
  const latestAnswer = item.user_answers?.length ? item.user_answers[item.user_answers.length - 1] : null;

  // 同类题生成状态
  const [similarCards, setSimilarCards] = useState<Array<SimilarQuestionItem | null> | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const aiQuestionId = item.id;

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
      <Space style={{ marginBottom: 8 }}>
        <Tag color="purple">{item.question_type || "未知题型"}</Tag>
        {DIFFICULTY_MAP[item.difficulty] && (
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

      <Typography.Paragraph style={{ fontSize: 13, marginBottom: 8 }}>
        {item.question_text}
      </Typography.Paragraph>

      {item.knowledge_point && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          知识点：{item.knowledge_point}
        </Typography.Text>
      )}

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

      <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 8 }}>
        {new Date(item.created_at).toLocaleString("zh-CN")}
      </Typography.Text>
    </Card>
  );
}
