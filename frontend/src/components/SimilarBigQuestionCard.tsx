import { useState } from "react";
import {
  Card, Tag, Button, Typography, Space, Radio, Checkbox,
  Input, Upload, message, Divider, Spin, Select,
} from "antd";
import { ReloadOutlined, UploadOutlined, CheckOutlined, SaveOutlined } from "@ant-design/icons";
import type { SimilarBigQuestion, SimilarBigSubQuestion } from "../services/questionService";
import { aiQuestionService } from "../services/aiQuestionService";
import MathText from "./MathText";
import QuestionSvgImage from "./QuestionSvgImage";

const DIFFICULTY_OPTIONS = [
  { value: "easy", label: "基础" },
  { value: "medium", label: "中等" },
  { value: "hard", label: "拔高" },
];

const DIFFICULTY_MAP: Record<string, string> = {
  easy: "基础",
  medium: "中等",
  hard: "拔高",
};

interface Props {
  /** 大题数据，null表示生成中 */
  question: SimilarBigQuestion | null;
  /** 来源题目ID */
  questionId: number;
  /** 换一题回调，传递难度 */
  onReplace: (difficulty: string) => void;
}

/** 单个子题的作答状态 */
interface SubAnswerState {
  selectedOptions: string[];
  textAnswer: string;
  imageFile: File | null;
  result: {
    is_correct: boolean;
    score: number;
    full_score: number;
    feedback: string;
    question_id?: number;
  } | null;
}

export default function SimilarBigQuestionCard({ question, questionId, onReplace }: Props) {
  const [answers, setAnswers] = useState<Record<number, SubAnswerState>>({});
  const [submitting, setSubmitting] = useState(false);
  const [changingDifficulty, setChangingDifficulty] = useState("medium");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  if (!question) {
    return (
      <Card size="small" style={{ minHeight: 200, marginTop: 16 }}>
        <Typography.Text strong style={{ fontSize: 14 }}>类似大题</Typography.Text>
        <Spin style={{ display: "block", margin: "30px auto" }} />
      </Card>
    );
  }

  const subQuestions = question.sub_questions || [];
  const allSubmitted = Object.values(answers).some((a) => a.result);
  const totalScore = subQuestions.reduce((sum, sq) => sum + (sq.full_score || 0), 0);
  const earnedScore = Object.values(answers).reduce(
    (sum, a) => sum + (a.result?.score || 0), 0
  );

  /** 默认子题作答状态（无副作用，供 setState updater 内联使用） */
  const defaultAnswerState = (): SubAnswerState => ({
    selectedOptions: [],
    textAnswer: "",
    imageFile: null,
    result: null,
  });

  /** 读取子题当前作答；未初始化时返回默认值（不触发 setState，提交快照用） */
  const getAnswer = (index: number): SubAnswerState => answers[index] || defaultAnswerState();

  const updateAnswer = (index: number, patch: Partial<SubAnswerState>) => {
    // 直接在 updater 内基于 prev 合并，禁止在 updater 里再读外层闭包 answers
    //（stale closure 会导致提交评分返回后合并的是旧快照，用户新输入被回退丢失）
    setAnswers((prev) => ({
      ...prev,
      [index]: { ...(prev[index] || defaultAnswerState()), ...patch },
    }));
  };

  /** 提交单个子题的作答 */
  const submitSubQuestion = async (index: number, sq: SimilarBigSubQuestion) => {
    const ans = getAnswer(index);
    const isChoice = sq.question_type?.includes("选") || (sq.options && sq.options.length > 0);

    if (isChoice && ans.selectedOptions.length === 0) {
      message.warning(`小题 ${index + 1}：请至少选择一个选项`);
      return;
    }
    if (!isChoice && !ans.textAnswer.trim() && !ans.imageFile) {
      message.warning(`小题 ${index + 1}：请输入答案或上传图片`);
      return;
    }

    setSubmitting(true);
    try {
      const res = await aiQuestionService.submitWithQuestion({
        source_question_id: questionId,
        question_text: sq.question_text,
        answer: sq.answer,
        analysis: sq.analysis,
        question_type: sq.question_type,
        knowledge_point: sq.knowledge_point,
        difficulty: sq.difficulty,
        options: sq.options || [],
        image_svg: sq.image_svg,
        selected_options: isChoice ? ans.selectedOptions : undefined,
        answer_text: !isChoice ? ans.textAnswer : undefined,
        answer_image: ans.imageFile || undefined,
      });
      updateAnswer(index, { result: res });
      message.success(`小题 ${index + 1} 评分完成`);
    } catch (e: any) {
      message.error(`小题 ${index + 1} 提交失败: ` + (e?.response?.data?.detail || e?.message || "未知错误"));
    } finally {
      setSubmitting(false);
    }
  };

  /** 换一题：按选择难度重新生成 */
  const handleReplace = () => {
    setAnswers({});
    setSaved(false);
    onReplace(changingDifficulty);
  };

  /** 确认保存到繁星驱动 */
  const handleSave = async () => {
    if (!question) return;
    setSaving(true);
    try {
      const res = await aiQuestionService.saveBigQuestion({
        source_question_id: questionId,
        question_context: question.question_context,
        context_image_svg: question.context_image_svg,
        difficulty: question.sub_questions[0]?.difficulty || "medium",
        sub_questions: question.sub_questions.map((sq, index) => ({
          ...sq,
          // 复用作答时已创建的题目记录，以保留已有作答
          existing_question_id: answers[index]?.result?.question_id ?? null,
        })),
      });
      message.success(res.message || `已保存 ${res.count} 道子题到繁星驱动`);
      setSaved(true);
    } catch (e: any) {
      message.error("保存失败: " + (e?.response?.data?.detail || e?.message || "未知错误"));
    } finally {
      setSaving(false);
    }
  };

  /** 判断是否为单选题/多选题 */
  const isChoiceType = (sq: SimilarBigSubQuestion) =>
    sq.question_type?.includes("选") || (sq.options && sq.options.length > 0);
  const isMultiType = (sq: SimilarBigSubQuestion) => sq.question_type?.includes("多选");

  return (
    <Card
      size="small"
      style={{ marginTop: 16 }}
      title={
        <Space>
          <Typography.Text strong style={{ fontSize: 14 }}>
            类似大题
          </Typography.Text>
          <Tag color="blue">{DIFFICULTY_MAP[question.sub_questions[0]?.difficulty] || "中等"}难度</Tag>
          {totalScore > 0 && (
            <Tag color="purple">共 {totalScore} 分</Tag>
          )}
        </Space>
      }
      extra={
        <Space size={8}>
          <Select
            size="small"
            value={changingDifficulty}
            onChange={(v) => setChangingDifficulty(v)}
            options={DIFFICULTY_OPTIONS}
            style={{ width: 80 }}
          />
          <Button
            size="small"
            icon={<ReloadOutlined />}
            onClick={handleReplace}
            disabled={submitting}
          >
            换一题
          </Button>
        </Space>
      }
    >
      {/* 大题背景材料 */}
      {question.question_context && (
        <div
          style={{
            padding: 12,
            background: "#fafafa",
            borderRadius: 6,
            border: "1px solid #e8e8e8",
            marginBottom: 16,
            whiteSpace: "pre-wrap",
            fontSize: 13,
            lineHeight: 1.8,
          }}
        >
          <Typography.Text strong style={{ fontSize: 13, display: "block", marginBottom: 4 }}>
            📖 阅读材料
          </Typography.Text>
          <MathText content={question.question_context} style={{ fontSize: 13 }} />
          {/* AI 生成的大题背景配图（SVG） */}
          {question.context_image_svg && <QuestionSvgImage svg={question.context_image_svg} />}
        </div>
      )}

      {/* 各子题 */}
      {subQuestions.map((sq, index) => {
        const ans = answers[index] || {
          selectedOptions: [] as string[],
          textAnswer: "",
          imageFile: null as File | null,
          result: null,
        };
        const isChoice = isChoiceType(sq);
        const isMulti = isMultiType(sq);
        const options = sq.options || [];

        return (
          <Card
            key={index}
            size="small"
            style={{
              marginBottom: 12,
              borderLeft: ans.result
                ? ans.result.is_correct
                  ? "3px solid #52c41a"
                  : "3px solid #ff4d4f"
                : "3px solid #d9d9d9",
            }}
            title={
              <Space size={4}>
                <Typography.Text strong style={{ fontSize: 13 }}>
                  小题 {index + 1}
                </Typography.Text>
                {isMulti ? (
                  <Tag color="red" style={{ fontSize: 11 }}>多选题</Tag>
                ) : isChoice ? (
                  <Tag color="blue" style={{ fontSize: 11 }}>单选题</Tag>
                ) : (
                  <Tag color="purple" style={{ fontSize: 11 }}>{sq.question_type || "未知"}</Tag>
                )}
                {sq.full_score > 0 && (
                  <Tag style={{ fontSize: 11 }}>分值：{sq.full_score}分</Tag>
                )}
              </Space>
            }
          >
            {/* 题目文字 */}
            <MathText
              content={sq.question_text}
              style={{ display: "block", marginBottom: 12, fontSize: 13 }}
            />

            {/* AI 生成的子题配图（SVG） */}
            {sq.image_svg && <QuestionSvgImage svg={sq.image_svg} />}

            {/* 单选题/多选题选项 */}
            {isChoice && options.length > 0 && !ans.result && (
              <div style={{ marginBottom: 12 }}>
                {isMulti ? (
                  <>
                    <Typography.Text type="danger" style={{ fontSize: 12, display: "block", marginBottom: 6, background: "#fff2f0", padding: "4px 8px", borderRadius: 4 }}>
                      ⚠ 此题为多选题，请选择所有正确答案
                    </Typography.Text>
                    <Checkbox.Group
                      value={ans.selectedOptions}
                      onChange={(vals) => updateAnswer(index, { selectedOptions: vals as string[] })}
                    >
                      <Space direction="vertical" size={4}>
                        {options.map((opt) => (
                          <Checkbox key={opt.label} value={opt.label}>
                            <Typography.Text strong>{opt.label}.</Typography.Text>{" "}
                            <MathText content={opt.text} />
                          </Checkbox>
                        ))}
                      </Space>
                    </Checkbox.Group>
                  </>
                ) : (
                  <Radio.Group
                    value={ans.selectedOptions[0]}
                    onChange={(e) => updateAnswer(index, { selectedOptions: [e.target.value] })}
                  >
                    <Space direction="vertical" size={4}>
                      {options.map((opt) => (
                        <Radio key={opt.label} value={opt.label}>
                          <Typography.Text strong>{opt.label}.</Typography.Text>{" "}
                          <MathText content={opt.text} />
                        </Radio>
                      ))}
                    </Space>
                  </Radio.Group>
                )}
              </div>
            )}

            {/* 主观题作答区 */}
            {!isChoice && !ans.result && (
              <div style={{ marginBottom: 12 }}>
                <Input.TextArea
                  rows={3}
                  placeholder="请输入你的答案..."
                  value={ans.textAnswer}
                  onChange={(e) => updateAnswer(index, { textAnswer: e.target.value })}
                />
                <Upload
                  beforeUpload={(file) => {
                    updateAnswer(index, { imageFile: file });
                    return false;
                  }}
                  maxCount={1}
                  showUploadList={!!ans.imageFile}
                  onRemove={() => updateAnswer(index, { imageFile: null })}
                  style={{ marginTop: 8 }}
                >
                  <Button icon={<UploadOutlined />} size="small" style={{ marginTop: 8 }}>
                    上传答案图片
                  </Button>
                </Upload>
              </div>
            )}

            {/* 评分结果 */}
            {ans.result && (
              <div
                style={{
                  padding: 12,
                  background: ans.result.is_correct ? "#f6ffed" : "#fff2f0",
                  borderRadius: 6,
                  border: `1px solid ${ans.result.is_correct ? "#b7eb8f" : "#ffccc7"}`,
                }}
              >
                <Typography.Text strong style={{ color: ans.result.is_correct ? "#52c41a" : "#ff4d4f" }}>
                  {ans.result.is_correct ? "✓ 回答正确！" : "✗ 回答错误"}
                </Typography.Text>
                <Typography.Text style={{ marginLeft: 8, fontSize: 13 }}>
                  得分：{ans.result.score}/{ans.result.full_score}
                </Typography.Text>
                {ans.result.feedback && (
                  <Typography.Paragraph style={{ marginTop: 4, fontSize: 13, marginBottom: 0 }}>
                    {ans.result.feedback}
                  </Typography.Paragraph>
                )}
                {/* 显示用户作答 */}
                {isChoice && ans.selectedOptions.length > 0 && (
                  <div style={{ marginTop: 4, paddingTop: 4, borderTop: "1px dashed #d9d9d9" }}>
                    <Typography.Text strong style={{ fontSize: 12, color: "#1677ff" }}>
                      你的选择：
                    </Typography.Text>
                    <Typography.Text style={{ fontSize: 12, marginLeft: 4 }}>
                      {ans.selectedOptions.sort().join("、")}
                    </Typography.Text>
                  </div>
                )}
                {!isChoice && ans.textAnswer && (
                  <div style={{ marginTop: 4, paddingTop: 4, borderTop: "1px dashed #d9d9d9" }}>
                    <Typography.Text strong style={{ fontSize: 12, color: "#1677ff" }}>
                      你的答案：
                    </Typography.Text>
                    <Typography.Paragraph style={{ fontSize: 12, marginBottom: 0, marginTop: 4, whiteSpace: "pre-wrap" }}>
                      {ans.textAnswer}
                    </Typography.Paragraph>
                  </div>
                )}
                {/* 正确答案 */}
                <div style={{ marginTop: 4, paddingTop: 4, borderTop: "1px dashed #d9d9d9" }}>
                  <Typography.Text strong style={{ fontSize: 12 }}>
                    正确答案：
                  </Typography.Text>
                  <MathText content={sq.answer} style={{ fontSize: 12 }} />
                </div>
                {/* 完整解析 */}
                {sq.analysis && (
                  <div style={{ marginTop: 4, paddingTop: 4, borderTop: "1px dashed #d9d9d9" }}>
                    <Typography.Text strong style={{ fontSize: 12, color: "#722ed1" }}>
                      解析：
                    </Typography.Text>
                    <MathText content={sq.analysis} style={{ display: "block", fontSize: 12, marginTop: 4 }} />
                  </div>
                )}
              </div>
            )}

            {/* 提交按钮 */}
            {!ans.result && (
              <Button
                type="primary"
                size="small"
                icon={<CheckOutlined />}
                loading={submitting}
                onClick={() => submitSubQuestion(index, sq)}
              >
                提交小题 {index + 1}
              </Button>
            )}
          </Card>
        );
      })}

      {/* 总分汇总 + 确认保存 */}
      {allSubmitted && (
        <>
          <Divider style={{ margin: "12px 0" }} />
          <div
            style={{
              padding: 12,
              background: earnedScore >= totalScore * 0.6 ? "#f6ffed" : "#fff2f0",
              borderRadius: 6,
              border: `1px solid ${earnedScore >= totalScore * 0.6 ? "#b7eb8f" : "#ffccc7"}`,
              textAlign: "center",
            }}
          >
            <Typography.Text strong style={{ fontSize: 15 }}>
              大题总分：{earnedScore} / {totalScore} 分
            </Typography.Text>
          </div>
          <div style={{ textAlign: "center", marginTop: 12 }}>
            <Button
              type="primary"
              size="middle"
              icon={<SaveOutlined />}
              loading={saving}
              disabled={saved}
              onClick={handleSave}
            >
              {saved ? "已保存到繁星驱动" : "确认保存到繁星驱动"}
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}
