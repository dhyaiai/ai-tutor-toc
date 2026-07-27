/**
 * 分步讲解卡片组件
 *
 * 嵌入在题目卡片下方，展示 AI 分步讲解内容。
 * 每步展示一个核心要点，用户通过"听懂了/没听懂"按钮反馈，
 * 反馈后自动记录到知识状态系统并推进到下一步。
 *
 * 全部步骤完成后提供：
 * - 最终总结
 * - "在聊天中继续追问"按钮（跳转到 ChatDrawer）
 * - 关联知识点标签
 */

import { useState, useCallback } from "react";
import { Button, Steps, Typography, Tag, Space, message, Spin } from "antd";
import {
  SmileOutlined,
  FrownOutlined,
  MessageOutlined,
} from "@ant-design/icons";
import { explainExercise, recordFeedback } from "../services/aiTutorService";

const { Text, Paragraph, Title } = Typography;

/** 单步讲解数据结构 */
interface ExplainStep {
  step_number: number;
  title: string;
  content: string;
  key_point: string;
  follow_up_question: string;
}

interface Props {
  /** 题目内容（题干） */
  exerciseContent: string;
  /** 所属学科 */
  subject?: string;
  /** 题目ID（用于关联） */
  questionId?: number;
  /** 讲解风格 */
  style?: "分步引导式" | "直接讲解式" | "基础科普式";
  /** 是否可见 */
  visible: boolean;
  /** 跳转到聊天继续追问的回调 */
  onContinueInChat?: () => void;
}

export default function ExplainCard({
  exerciseContent,
  subject = "未知",
  questionId,
  style = "分步引导式",
  visible,
  onContinueInChat,
}: Props) {
  /** 分步讲解数据 */
  const [steps, setSteps] = useState<ExplainStep[]>([]);
  /** 当前步骤索引（从0开始） */
  const [currentStep, setCurrentStep] = useState(0);
  /** 是否正在加载讲解 */
  const [loading, setLoading] = useState(false);
  /** 是否已完成全部讲解 */
  const [completed, setCompleted] = useState(false);
  /** 知识点列表 */
  const [knowledgePoints, setKnowledgePoints] = useState<string[]>([]);
  /** 最终总结 */
  const [finalSummary, setFinalSummary] = useState("");
  /** 是否已经开始讲解 */
  const [started, setStarted] = useState(false);

  /**
   * 开始讲解：调用后端 API 获取分步讲解内容
   */
  const startExplain = useCallback(async () => {
    if (!exerciseContent) return;

    setLoading(true);
    setStarted(true);
    setCurrentStep(0);
    setCompleted(false);

    try {
      const result = await explainExercise({
        exercise_content: exerciseContent,
        subject,
        explanation_style: style,
        card_mode: true,
        strict_level: 3,
      });

      setSteps(result.steps || []);
      setKnowledgePoints(result.knowledge_points || []);
      setFinalSummary(result.final_summary || "");
    } catch (err) {
      console.error("加载讲解失败:", err);
      message.error("讲解加载失败，请重试");
    } finally {
      setLoading(false);
    }
  }, [exerciseContent, subject, style]);

  /**
   * 用户点击"听懂了"或"没听懂"反馈
   * 记录反馈到知识状态系统，然后推进到下一步
   */
  const handleFeedback = useCallback(
    async (level: "完全听懂" | "部分听懂" | "没听懂") => {
      // 记录反馈
      const currentKP = knowledgePoints[0] || "";
      try {
        await recordFeedback({
          knowledge_point: currentKP,
          feedback_level: level,
          question_id: questionId ? String(questionId) : undefined,
        });
      } catch {
        // 反馈记录失败不阻断流程
      }

      // 推进到下一步
      if (currentStep < steps.length - 1) {
        setCurrentStep((prev) => prev + 1);
      } else {
        // 全部完成
        setCompleted(true);
      }
    },
    [currentStep, steps.length, knowledgePoints, questionId]
  );

  if (!visible) return null;

  return (
    <div
      style={{
        marginTop: 16,
        padding: "12px 16px",
        background: "#fafafa",
        borderRadius: 8,
        border: "1px solid #e8e8e8",
      }}
    >
      {/* 未开始时显示开始按钮 */}
      {!started && !loading && (
        <div style={{ textAlign: "center" }}>
          <Button
            type="primary"
            icon={<MessageOutlined />}
            onClick={startExplain}
          >
            AI 分步讲解
          </Button>
        </div>
      )}

      {/* 加载中 */}
      {loading && (
        <div style={{ textAlign: "center", padding: "20px 0" }}>
          <Spin tip="AI 正在准备讲解..." />
        </div>
      )}

      {/* 讲解进行中 */}
      {started && !loading && !completed && steps.length > 0 && (
        <div>
          {/* 步骤进度指示 */}
          <Steps
            size="small"
            current={currentStep}
            items={steps.map((s) => ({
              title: s.title,
            }))}
            style={{ marginBottom: 16 }}
          />

          {/* 当前步骤内容 */}
          <div style={{ marginBottom: 12 }}>
            <Text strong style={{ fontSize: 13, color: "#1677ff" }}>
              第{steps[currentStep].step_number}步：{steps[currentStep].title}
            </Text>
            <Paragraph
              style={{
                marginTop: 8,
                padding: "8px 12px",
                background: "#fff",
                borderRadius: 6,
                border: "1px solid #f0f0f0",
                whiteSpace: "pre-wrap",
                fontSize: 14,
              }}
            >
              {steps[currentStep].content}
            </Paragraph>

            {/* 关键点提示 */}
            {steps[currentStep].key_point && (
              <Text
                type="secondary"
                style={{ fontSize: 12, display: "block", marginTop: 4 }}
              >
                💡 {steps[currentStep].key_point}
              </Text>
            )}

            {/* 追问 */}
            {steps[currentStep].follow_up_question && (
              <Text
                style={{
                  fontSize: 13,
                  display: "block",
                  marginTop: 8,
                  color: "#fa8c16",
                }}
              >
                ❓ {steps[currentStep].follow_up_question}
              </Text>
            )}
          </div>

          {/* 反馈按钮 */}
          <Space style={{ marginTop: 12 }}>
            <Button
              type="primary"
              size="small"
              icon={<SmileOutlined />}
              onClick={() => handleFeedback("完全听懂")}
            >
              听懂了
            </Button>
            <Button
              size="small"
              icon={<FrownOutlined />}
              onClick={() => handleFeedback("没听懂")}
            >
              没听懂
            </Button>
          </Space>
        </div>
      )}

      {/* 全部完成 */}
      {completed && (
        <div>
          <Text type="success" strong>
            ✅ 讲解完成
          </Text>

          {/* 知识点标签 */}
          {knowledgePoints.length > 0 && (
            <div style={{ marginTop: 8, marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                关联知识点：
              </Text>
              {knowledgePoints.map((kp, i) => (
                <Tag key={i} color="blue" style={{ marginTop: 4 }}>
                  {kp}
                </Tag>
              ))}
            </div>
          )}

          {/* 最终总结 */}
          {finalSummary && (
            <Paragraph
              style={{
                marginTop: 12,
                padding: "8px 12px",
                background: "#fffbe6",
                borderRadius: 6,
                border: "1px solid #ffe58f",
                fontSize: 13,
              }}
            >
              📝 {finalSummary}
            </Paragraph>
          )}

          {/* 在聊天中追问 */}
          {onContinueInChat && (
            <Button
              type="link"
              icon={<MessageOutlined />}
              onClick={onContinueInChat}
              style={{ marginTop: 8 }}
            >
              在聊天中继续追问
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
