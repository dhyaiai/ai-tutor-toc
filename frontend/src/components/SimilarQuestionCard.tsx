import { useState } from "react";
import {
  Card, Tag, Button, Typography, Space, Skeleton, Radio, Checkbox,
  Input, Upload, message, Spin,
} from "antd";
import { ReloadOutlined, UploadOutlined, CheckOutlined } from "@ant-design/icons";
import type { SimilarQuestionItem } from "../services/questionService";
import { aiQuestionService } from "../services/aiQuestionService";

interface Props {
  index: number;
  question: SimilarQuestionItem | null;
  questionId: number; // source question id
  onReplace: (index: number) => void;
}

const DIFFICULTY_MAP: Record<string, { label: string; color: string }> = {
  easy: { label: "基础", color: "green" },
  medium: { label: "中等", color: "orange" },
  hard: { label: "拔高", color: "red" },
};

export default function SimilarQuestionCard({
  index, question, questionId, onReplace,
}: Props) {
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [textAnswer, setTextAnswer] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{
    is_correct: boolean;
    score: number;
    full_score: number;
    feedback: string;
    correct_answer: string;
  } | null>(null);
  const [showAnswer, setShowAnswer] = useState(false);
  const [retrying, setRetrying] = useState(false);

  if (!question) {
    return (
      <Card size="small" style={{ minHeight: 200 }}>
        <Typography.Text strong style={{ fontSize: 14 }}>题目 {index + 1}</Typography.Text>
        <Skeleton active paragraph={{ rows: 4 }} style={{ marginTop: 12 }} />
      </Card>
    );
  }

  // "单选题"/"多选题" 都包含"选"字；或用 options 列表兜底
  const isChoice = question.question_type?.includes("选") || (question.options && question.options.length > 0);
  const isMulti = question.question_type?.includes("多选");
  const options = question.options || [];
  const isFailed = question.question_text?.includes("生成失败");

  const handleRetry = async () => {
    setRetrying(true);
    try {
      onReplace(index);
    } finally {
      setRetrying(false);
    }
  };

  const handleSubmit = async () => {
    if (isChoice && selectedOptions.length === 0) {
      message.warning("请至少选择一个选项");
      return;
    }
    if (!isChoice && !textAnswer.trim() && !imageFile) {
      message.warning("请输入答案或上传图片");
      return;
    }

    setSubmitting(true);
    try {
      const res = await aiQuestionService.submitWithQuestion({
        source_question_id: questionId,
        question_text: question.question_text,
        answer: question.answer,
        analysis: question.analysis,
        question_type: question.question_type,
        knowledge_point: question.knowledge_point,
        difficulty: question.difficulty,
        options: question.options || [],
        selected_options: isChoice ? selectedOptions : undefined,
        answer_text: !isChoice ? textAnswer : undefined,
        answer_image: imageFile || undefined,
      });
      setResult(res);
      setShowAnswer(true);
    } catch (e: any) {
      message.error("提交失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleReplace = () => {
    setSelectedOptions([]);
    setTextAnswer("");
    setImageFile(null);
    setResult(null);
    setShowAnswer(false);
    onReplace(index);
  };

  return (
    <Card
      size="small"
      style={{ minHeight: 200 }}
      extra={
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={handleReplace}
          disabled={submitting}
        >
          换一题
        </Button>
      }
      title={
        <Space size={4}>
          <Typography.Text strong style={{ fontSize: 14 }}>
            题目 {index + 1}
          </Typography.Text>
          {isMulti ? (
            <Tag color="red" style={{ fontSize: 11, fontWeight: "bold" }}>多选题</Tag>
          ) : isChoice ? (
            <Tag color="blue" style={{ fontSize: 11 }}>单选题</Tag>
          ) : (
            <Tag color="purple" style={{ fontSize: 11 }}>{question.question_type || "未知题型"}</Tag>
          )}
          {DIFFICULTY_MAP[question.difficulty] && (
            <Tag color={DIFFICULTY_MAP[question.difficulty].color} style={{ fontSize: 11 }}>
              {DIFFICULTY_MAP[question.difficulty].label}
            </Tag>
          )}
        </Space>
      }
    >
      {/* 题目内容 */}
      {isFailed ? (
        <div style={{ textAlign: "center", padding: "20px 0" }}>
          <Typography.Text type="danger" style={{ fontSize: 14, display: "block", marginBottom: 12 }}>
            此题生成失败
          </Typography.Text>
          <Button
            type="primary"
            size="small"
            loading={retrying}
            onClick={handleRetry}
          >
            重新生成此题
          </Button>
        </div>
      ) : (
        <>
          <Typography.Paragraph
            style={{ marginBottom: 12, fontSize: 13, whiteSpace: "pre-wrap" }}
          >
            {question.question_text}
          </Typography.Paragraph>

          {/* 单选题/多选题选项 */}
          {isChoice && options.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          {isMulti && (
            <Typography.Text type="danger" style={{ fontSize: 12, display: "block", marginBottom: 6, background: "#fff2f0", padding: "4px 8px", borderRadius: 4, border: "1px solid #ffccc7" }}>
              ⚠ 此题为多选题，请选择所有正确答案
            </Typography.Text>
          )}
          {isMulti ? (
            <Checkbox.Group
              value={selectedOptions}
              onChange={(vals) => setSelectedOptions(vals as string[])}
              disabled={!!result}
            >
              <Space direction="vertical" size={4}>
                {options.map((opt) => (
                  <Checkbox key={opt.label} value={opt.label}>
                    <Typography.Text strong>{opt.label}.</Typography.Text> {opt.text}
                  </Checkbox>
                ))}
              </Space>
            </Checkbox.Group>
          ) : (
            <Radio.Group
              value={selectedOptions[0]}
              onChange={(e) => setSelectedOptions([e.target.value])}
              disabled={!!result}
            >
              <Space direction="vertical" size={4}>
                {options.map((opt) => (
                  <Radio key={opt.label} value={opt.label}>
                    <Typography.Text strong>{opt.label}.</Typography.Text> {opt.text}
                  </Radio>
                ))}
              </Space>
            </Radio.Group>
          )}
        </div>
      )}

      {/* 填空/解答题输入 */}
      {!isChoice && !result && (
        <div style={{ marginBottom: 12 }}>
          <Input.TextArea
            rows={3}
            placeholder="请输入你的答案..."
            value={textAnswer}
            onChange={(e) => setTextAnswer(e.target.value)}
          />
          <Upload
            beforeUpload={(file) => {
              setImageFile(file);
              return false;
            }}
            maxCount={1}
            showUploadList={!!imageFile}
            onRemove={() => setImageFile(null)}
            style={{ marginTop: 8 }}
          >
            <Button icon={<UploadOutlined />} size="small" style={{ marginTop: 8 }}>
              上传答案图片
            </Button>
          </Upload>
        </div>
      )}

      {/* 提交按钮 */}
      {!result && (
        <Button
          type="primary"
          size="small"
          icon={<CheckOutlined />}
          loading={submitting}
          onClick={handleSubmit}
        >
          确认提交
        </Button>
      )}

      {/* 评分结果 */}
      {result && (
        <div
          style={{
            marginTop: 12,
            padding: 12,
            background: result.is_correct ? "#f6ffed" : "#fff2f0",
            borderRadius: 6,
            border: `1px solid ${result.is_correct ? "#b7eb8f" : "#ffccc7"}`,
          }}
        >
          <Typography.Text strong style={{ color: result.is_correct ? "#52c41a" : "#ff4d4f" }}>
            {result.is_correct ? "回答正确！" : "回答错误"}
          </Typography.Text>
          {result.score != null && (
            <Typography.Text style={{ marginLeft: 8, fontSize: 13 }}>
              得分：{result.score}/{result.full_score}
            </Typography.Text>
          )}
          {result.feedback && (
            <Typography.Paragraph style={{ marginTop: 4, fontSize: 13, marginBottom: 0 }}>
              {result.feedback}
            </Typography.Paragraph>
          )}

          {/* 显示用户作答 */}
          {!isChoice && textAnswer && (
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #d9d9d9" }}>
              <Typography.Text strong style={{ fontSize: 13, color: "#1677ff" }}>你的答案：</Typography.Text>
              <Typography.Paragraph style={{ fontSize: 13, marginBottom: 0, marginTop: 4, whiteSpace: "pre-wrap" }}>
                {textAnswer}
              </Typography.Paragraph>
            </div>
          )}
          {isChoice && selectedOptions.length > 0 && (
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #d9d9d9" }}>
              <Typography.Text strong style={{ fontSize: 13, color: "#1677ff" }}>你的选择：</Typography.Text>
              <Typography.Text style={{ fontSize: 13, marginLeft: 8 }}>
                {selectedOptions.sort().join("、")}
              </Typography.Text>
            </div>
          )}

          {/* 显示正确答案 */}
          {showAnswer && question.answer && (
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #d9d9d9" }}>
              <Typography.Text strong style={{ fontSize: 13 }}>正确答案：</Typography.Text>
              <Typography.Text style={{ fontSize: 13 }}>{question.answer}</Typography.Text>
            </div>
          )}

          {/* 显示完整解析 */}
          {showAnswer && question.analysis && (
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #d9d9d9" }}>
              <Typography.Text strong style={{ fontSize: 13, color: "#722ed1" }}>解析：</Typography.Text>
              <Typography.Paragraph style={{ fontSize: 13, marginBottom: 0, marginTop: 4, whiteSpace: "pre-wrap" }}>
                {question.analysis}
              </Typography.Paragraph>
            </div>
          )}
        </div>
      )}

          {/* 知识点 */}
          {question.knowledge_point && (
            <div style={{ marginTop: 8 }}>
              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                知识点：{question.knowledge_point}
              </Typography.Text>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
