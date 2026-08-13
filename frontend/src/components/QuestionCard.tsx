import { Card, Tag, Button, Space, Typography, Popconfirm, Popover, Image, Descriptions, Input, message, Spin, Collapse, Tooltip } from "antd";
import { ReloadOutlined, DeleteOutlined, ExpandOutlined, SyncOutlined, PlusOutlined } from "@ant-design/icons";
import { useState } from "react";
import { useReanalysis } from "../hooks/useReanalysis";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { questionService } from "../services/questionService";
import { QUESTION_STATUS_MAP } from "../utils/constants";
import { AI_EXPLAIN_SUBJECTS } from "../utils/filterConfig";
import { getScoreRate } from "../utils/helpers";
import ManualSplitModal from "./ManualSplitModal";
import ExplainCard from "./ExplainCard";
import MathText from "./MathText";
import type { QuestionItem } from "../services/assignmentService";

interface Props {
  question: QuestionItem;
  assignmentId: number;
  assignmentStatus: string;
  /** 作业科目：仅无公式科目（语文/英语/生物/政治/历史/地理）显示 助教讲解入口 */
  subject?: string;
}

/** 提取知识点名称列表（兼容数组/对象两种存储格式） */
function kpNames(q: QuestionItem): string[] {
  if (!q.knowledge_points) return [];
  const list = Array.isArray(q.knowledge_points)
    ? q.knowledge_points
    : Object.values(q.knowledge_points);
  return list.map((kp: unknown) =>
    typeof kp === "string" ? kp : (kp as { name?: string })?.name || String(kp)
  );
}

/**
 * 拼接题目上下文文本供 AI 讲解。
 * 题干优先用识别出的 question_text（含公式）；老数据无题干文本时
 * 回退到用批改结果字段组合出讲解上下文；大题套小题时逐小题拼接。
 */
function buildExplainContent(q: QuestionItem): string {
  const one = (item: QuestionItem, label: string): string => {
    const kps = kpNames(item);
    return [
      `${label}${item.question_type ? `（${item.question_type}）` : ""}`,
      item.question_text ? `题目：${item.question_text}` : "",
      item.correct_answer ? `正确答案：${item.correct_answer}` : "",
      item.student_answer ? `学生答案：${item.student_answer}` : "",
      item.analysis_detail ? `题目解析：${item.analysis_detail}` : "",
      kps.length ? `涉及知识点：${kps.join("、")}` : "",
    ].filter(Boolean).join("\n");
  };

  if (q.children && q.children.length > 0) {
    const sorted = [...q.children].sort(
      (a, b) => (a.sub_question_index ?? 0) - (b.sub_question_index ?? 0)
    );
    return [
      `第 ${q.question_number} 题${q.question_type ? `（${q.question_type}）` : ""}，共 ${sorted.length} 小题：`,
      ...sorted.map((c, i) => one(c, `小题 ${i + 1}`)),
    ].join("\n\n");
  }
  return one(q, `第 ${q.question_number} 题`);
}

/**
 * 渲染题目状态 Tag：
 * 后端将当前批次题目标记为 processing，此时显示蓝色"正在分析"+ 转圈动画；
 * 尚未轮到的题目保持灰色"待分析"
 */
function QuestionStatusTag({
  status,
  small,
}: {
  status: string;
  small?: boolean;
}) {
  const fontSize = small ? 11 : undefined;
  if (status === "processing") {
    return (
      <Tag icon={<SyncOutlined spin />} color="processing" style={{ fontSize }}>
        正在分析
      </Tag>
    );
  }
  const statusCfg = QUESTION_STATUS_MAP[status] || { color: "default", label: status };
  return (
    <Tag color={statusCfg.color} style={{ fontSize }}>
      {statusCfg.label}
    </Tag>
  );
}

/**
 * 子题卡片（嵌套在父题内部的紧凑版）
 * 只显示必要信息：序号、得分、答案、知识点、分析
 */
function SubQuestionCard({
  question,
}: {
  question: QuestionItem;
}) {
  const subIndex = question.sub_question_index != null ? question.sub_question_index + 1 : "?";

  return (
    <Card
      size="small"
      style={{ marginBottom: 8, borderLeft: "3px solid #1890ff" }}
      title={
        <Space size={4}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            小题 {subIndex}
          </Typography.Text>
          {question.question_type && (
            <Tag color="purple" style={{ fontSize: 11 }}>{question.question_type}</Tag>
          )}
          <QuestionStatusTag status={question.status} small />
          {/* 失败题已有红色状态标签，置信度是失败兜底值，不另显低置信度 */}
          {question.status !== "failed" && question.confidence_score != null && question.confidence_score < 0.7 && (
            <Tooltip title="AI 对识别结果把握不足。可点击卡片右上角「重新生成」，在备注中写明如「学生答案选D；正确答案是B」进行人工纠正。">
              <Tag color="orange" style={{ fontSize: 11 }}>低置信度</Tag>
            </Tooltip>
          )}
        </Space>
      }
    >
      <Descriptions column={2} size="small">
        <Descriptions.Item label="得分">
          {question.score != null ? `${question.score} / ${question.full_score}` : "-"}
        </Descriptions.Item>
        <Descriptions.Item label="得分率">
          {getScoreRate(question.score, question.full_score)}
        </Descriptions.Item>
        <Descriptions.Item label="学生答案">
          {question.student_answer ? <MathText content={question.student_answer} /> : "-"}
        </Descriptions.Item>
        <Descriptions.Item label="正确答案">
          {question.correct_answer ? <MathText content={question.correct_answer} /> : "-"}
        </Descriptions.Item>
      </Descriptions>
      {question.knowledge_points && (
        <div style={{ marginTop: 4 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>知识点：</Typography.Text>
          {(Array.isArray(question.knowledge_points)
            ? question.knowledge_points
            : Object.values(question.knowledge_points)
          ).map((kp: unknown, i: number) => {
            const name = typeof kp === "string" ? kp : (kp as { name?: string })?.name || String(kp);
            return (
              <Tag key={`${name}-${i}`} color="blue" style={{ fontSize: 11, marginTop: 2 }}>
                <MathText content={name} />
              </Tag>
            );
          })}
        </div>
      )}
      {question.common_mistakes && Array.isArray(question.common_mistakes) && question.common_mistakes.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>常见错误：</Typography.Text>
          {(question.common_mistakes as string[]).map((m: string, i: number) => (
            <Tag key={i} color="orange" style={{ fontSize: 11, marginTop: 2 }}><MathText content={m} /></Tag>
          ))}
        </div>
      )}
      {question.analysis_detail && (
        <Typography.Paragraph
          type="secondary"
          style={{ marginTop: 8, fontSize: 12 }}
          ellipsis={{ rows: 2, expandable: true }}
        >
          <MathText content={question.analysis_detail} />
        </Typography.Paragraph>
      )}
    </Card>
  );
}

/**
 * 题目卡片 — 支持三种模式：
 * 1. 有 children → 父题容器（大题套小题）
 * 2. 无 children 且 无 parent_id → 普通独立题
 * 3. 有 parent_id → 子题（正常不应独立出现，兜底显示）
 */
export default function QuestionCard({ question, assignmentId, assignmentStatus, subject }: Props) {
  const { reanalyze, reanalyzing } = useReanalysis(assignmentId);
  const queryClient = useQueryClient();
  const [adjustVisible, setAdjustVisible] = useState(false);
  const [insertVisible, setInsertVisible] = useState(false);
  const [remarkOpen, setRemarkOpen] = useState(false);
  const [remark, setRemark] = useState("");

  const deleteMutation = useMutation({
    mutationFn: () => questionService.delete(question.id),
    onSuccess: () => {
      message.success("题目已删除");
      queryClient.invalidateQueries({ queryKey: ["assignment"] });
      queryClient.invalidateQueries({ queryKey: ["assignments"] });
      queryClient.invalidateQueries({ queryKey: ["analytics"] });
    },
    onError: (err: any) => {
      message.error("删除失败: " + (err?.response?.data?.detail || err?.message || "未知错误"));
    },
  });

  const showEditTools = assignmentStatus === "splitted" || assignmentStatus === "completed";
  const hasBbox =
    question.bbox_x != null &&
    question.bbox_y != null &&
    question.bbox_w != null &&
    question.bbox_h != null;

  const hasChildren = question.children && question.children.length > 0;
  const isChild = question.parent_id != null;
  // 助教讲解：仅无公式科目且题目已完成分析时显示（公式科目 TTS 读不准，暂不开放）
  const showExplain =
    !!subject &&
    AI_EXPLAIN_SUBJECTS.includes(subject) &&
    question.status === "completed";

  // ── 模式3：子题（正常不应独立出现，兜底显示紧凑版）──
  if (isChild && !hasChildren) {
    return <SubQuestionCard question={question} />;
  }

  // ── 模式1：父题（有子题）──
  if (hasChildren) {
    const sortedChildren = [...question.children!].sort(
      (a, b) => (a.sub_question_index ?? 0) - (b.sub_question_index ?? 0)
    );
    // 大题分值 = 所有小题满分之和（大题本身不存分值）
    const totalFullScore = sortedChildren.reduce(
      (sum, c) => sum + (c.full_score ?? 0),
      0
    );
    // 大题得分 = 所有小题得分之和
    const totalScore = sortedChildren.reduce(
      (sum, c) => sum + (c.score ?? 0),
      0
    );

    return (
      <>
        <Card
          size="small"
          title={
            <Space>
              <Typography.Text strong>
                第 {question.question_number} 题（共 {sortedChildren.length} 小题）
              </Typography.Text>
              {question.question_type && (
                <Tag color="purple">{question.question_type}</Tag>
              )}
              {totalFullScore > 0 && (
                <Tag color="gold">得分：{totalScore} / {totalFullScore} 分</Tag>
              )}
              <QuestionStatusTag status={question.status} />
            </Space>
          }
          extra={
            <Space>
              {showEditTools && (
                <>
                  <Button
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={() => setInsertVisible(true)}
                    title="在当前题下方插入一道新题（补切漏切题目）"
                  >
                    下方插题
                  </Button>
                  {hasBbox && (
                    <Button
                      size="small"
                      icon={<ExpandOutlined />}
                      onClick={() => setAdjustVisible(true)}
                    >
                      调整区域
                    </Button>
                  )}
                  <Popconfirm
                    title="确认删除此大题及所有小题？"
                    description="删除后不可恢复"
                    onConfirm={() => deleteMutation.mutate()}
                  >
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      loading={deleteMutation.isPending}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </>
              )}
              <Popover
                open={remarkOpen}
                onOpenChange={(open) => {
                  if (reanalyzing === question.id) return;
                  setRemarkOpen(open);
                  if (!open) setRemark("");
                }}
                trigger="click"
                title={reanalyzing === question.id ? "重新生成中..." : "重新生成备注（可选）"}
                content={
                  <div style={{ width: 280 }}>
                    {reanalyzing === question.id ? (
                      <div style={{ textAlign: "center", padding: "20px 0" }}>
                        <Spin size="large" />
                        <div style={{ marginTop: 8, color: "#888" }}>AI 正在重新分析，请稍候...</div>
                      </div>
                    ) : (
                      <>
                        <Input.TextArea
                          rows={3}
                          value={remark}
                          onChange={(e) => setRemark(e.target.value)}
                          placeholder="告诉AI哪里识别有问题"
                        />
                        <Button
                          type="primary"
                          size="small"
                          style={{ marginTop: 8 }}
                          onClick={async () => {
                            await reanalyze(question.id, remark || undefined);
                            setRemarkOpen(false);
                            setRemark("");
                          }}
                        >
                          确认重新生成
                        </Button>
                      </>
                    )}
                  </div>
                }
              >
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  loading={reanalyzing === question.id}
                >
                  重新生成
                </Button>
              </Popover>
            </Space>
          }
        >
          {/* 父题拼接图 */}
          <div style={{ marginBottom: 16 }}>
            {question.question_text && (
              <MathText
                content={question.question_text}
                style={{ display: "block", marginBottom: 8, fontSize: 13 }}
              />
            )}
            {question.image_url && (
              <Image
                src={question.image_url}
                alt={`第${question.question_number}题`}
                width={300}
                style={{ borderRadius: 4 }}
                fallback="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
              />
            )}
            {question.knowledge_points && (
              <div style={{ marginTop: 8 }}>
                <Typography.Text type="secondary" strong style={{ fontSize: 12 }}>
                  涉及知识点：
                </Typography.Text>
                {(Array.isArray(question.knowledge_points)
                  ? question.knowledge_points
                  : Object.values(question.knowledge_points)
                ).map((kp: unknown, i: number) => {
                  const name = typeof kp === "string" ? kp : (kp as { name?: string })?.name || String(kp);
                  return (
                    <Tag key={`${name}-${i}`} color="blue" style={{ marginTop: 2 }}>
                      <MathText content={name} />
                    </Tag>
                  );
                })}
              </div>
            )}
          </div>

          {/* 子题列表 */}
          <div>
            <Typography.Text strong style={{ fontSize: 13, display: "block", marginBottom: 8 }}>
              各小题详情：
            </Typography.Text>
            {sortedChildren.map((child) => (
              <SubQuestionCard key={child.id} question={child} />
            ))}
          </div>

          {/* 助教讲解（含语音播报，仅无公式科目开放） */}
          {showExplain && (
            <ExplainCard
              exerciseContent={buildExplainContent(question)}
              subject={subject}
              questionId={question.id}
              visible
            />
          )}
        </Card>

        {hasBbox && (
          <ManualSplitModal
            assignmentId={assignmentId}
            visible={adjustVisible}
            prefillRegion={{
              question_id: question.id,
              question_number: question.question_number,
              page_index: question.page_index ?? 0,
              x: question.bbox_x!,
              y: question.bbox_y!,
              w: question.bbox_w!,
              h: question.bbox_h!,
            }}
            onSuccess={() => {
              setAdjustVisible(false);
              queryClient.invalidateQueries({ queryKey: ["assignment"] });
            }}
            onCancel={() => setAdjustVisible(false)}
          />
        )}

        <ManualSplitModal
          assignmentId={assignmentId}
          visible={insertVisible}
          insertAfter={{
            question_id: question.id,
            question_number: question.question_number,
            page_index: question.page_index ?? 0,
          }}
          onSuccess={() => {
            setInsertVisible(false);
            queryClient.invalidateQueries({ queryKey: ["assignment"] });
            queryClient.invalidateQueries({ queryKey: ["assignments"] });
          }}
          onCancel={() => setInsertVisible(false)}
        />
      </>
    );
  }

  // ── 模式2：普通独立题（保持原有渲染）──
  return (
    <>
      <Card
        size="small"
        title={
          <Space>
            <Typography.Text strong>第 {question.question_number} 题</Typography.Text>
            {question.question_type && (
              <Tag color="purple">{question.question_type}</Tag>
            )}
            <QuestionStatusTag status={question.status} />
            {/* 失败题已有红色状态标签，置信度是失败兜底值，不另显低置信度 */}
            {question.status !== "failed" && question.confidence_score != null && question.confidence_score < 0.7 && (
              <Tooltip title="AI 对识别结果把握不足。可点击卡片右上角「重新生成」，在备注中写明如「学生答案选D；正确答案是B」进行人工纠正。">
                <Tag color="orange">低置信度</Tag>
              </Tooltip>
            )}
          </Space>
        }
        extra={
          <Space>
            {showEditTools && (
              <>
                <Button
                  size="small"
                  icon={<PlusOutlined />}
                  onClick={() => setInsertVisible(true)}
                  title="在当前题下方插入一道新题（补切漏切题目）"
                >
                  下方插题
                </Button>
                {hasBbox && (
                  <Button
                    size="small"
                    icon={<ExpandOutlined />}
                    onClick={() => setAdjustVisible(true)}
                  >
                    调整区域
                  </Button>
                )}
                <Popconfirm
                  title="确认删除此题？"
                  description="删除后不可恢复"
                  onConfirm={() => deleteMutation.mutate()}
                >
                  <Button
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    loading={deleteMutation.isPending}
                  >
                    删除
                  </Button>
                </Popconfirm>
              </>
            )}
            <Popover
              open={remarkOpen}
              onOpenChange={(open) => {
                if (reanalyzing === question.id) return;
                setRemarkOpen(open);
                if (!open) setRemark("");
              }}
              trigger="click"
              title={reanalyzing === question.id ? "重新生成中..." : "重新生成备注（可选）"}
              content={
                <div style={{ width: 280 }}>
                  {reanalyzing === question.id ? (
                    <div style={{ textAlign: "center", padding: "20px 0" }}>
                      <Spin size="large" />
                      <div style={{ marginTop: 8, color: "#888" }}>AI 正在重新分析，请稍候...</div>
                    </div>
                  ) : (
                    <>
                      <Input.TextArea
                        rows={3}
                        value={remark}
                        onChange={(e) => setRemark(e.target.value)}
                        placeholder="输入人工审核备注，例如：学生答案选D；正确答案是B；得分5分。系统将自动识别并强制覆盖AI结果。"
                      />
                      <Button
                        type="primary"
                        size="small"
                        style={{ marginTop: 8 }}
                        onClick={async () => {
                          await reanalyze(question.id, remark || undefined);
                          setRemarkOpen(false);
                          setRemark("");
                        }}
                      >
                        确认重新生成
                      </Button>
                    </>
                  )}
                </div>
              }
            >
              <Button
                size="small"
                icon={<ReloadOutlined />}
                loading={reanalyzing === question.id}
              >
                重新生成
              </Button>
            </Popover>
          </Space>
        }
      >
        <div style={{ display: "flex", gap: 16 }}>
          {question.question_text && (
            <MathText
              content={question.question_text}
              style={{ display: "block", maxWidth: 360, fontSize: 13 }}
            />
          )}
          {question.image_url && (
            <Image
              src={question.image_url}
              alt={`第${question.question_number}题`}
              width={200}
              style={{ borderRadius: 4 }}
              fallback="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
            />
          )}
          <div style={{ flex: 1 }}>
            <Descriptions column={2} size="small">
              <Descriptions.Item label="得分">
                {question.score != null ? `${question.score} / ${question.full_score}` : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="得分率">
                {getScoreRate(question.score, question.full_score)}
              </Descriptions.Item>
              <Descriptions.Item label="学生答案">
                {question.student_answer ? <MathText content={question.student_answer} /> : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="正确答案">
                {question.correct_answer ? <MathText content={question.correct_answer} /> : "-"}
              </Descriptions.Item>
            </Descriptions>
            {question.knowledge_points && (
              <div style={{ marginTop: 8 }}>
                <Typography.Text type="secondary" strong style={{ fontSize: 12 }}>
                  知识点：
                </Typography.Text>
                {(Array.isArray(question.knowledge_points)
                  ? question.knowledge_points
                  : Object.values(question.knowledge_points)
                ).map((kp: unknown, i: number) => {
                  const name = typeof kp === "string" ? kp : (kp as { name?: string })?.name || String(kp);
                  return (
                    <Tag key={`${name}-${i}`} color="blue" style={{ marginTop: 4 }}>
                      <MathText content={name} />
                    </Tag>
                  );
                })}
              </div>
            )}
            {question.common_mistakes && Array.isArray(question.common_mistakes) && question.common_mistakes.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Typography.Text type="warning" strong style={{ fontSize: 12 }}>
                  常见错误：
                </Typography.Text>
                {(question.common_mistakes as string[]).map((m: string, i: number) => (
                  <Tag key={i} color="orange" style={{ marginTop: 4 }}>
                    <MathText content={m} />
                  </Tag>
                ))}
              </div>
            )}
            {question.analysis_detail && (
              <Typography.Paragraph
                type="secondary"
                style={{ marginTop: 8, fontSize: 13 }}
                ellipsis={{ rows: 2, expandable: true }}
              >
                <MathText content={question.analysis_detail} />
              </Typography.Paragraph>
            )}
          </div>
        </div>

        {/* 助教讲解（含语音播报，仅无公式科目开放） */}
        {showExplain && (
          <ExplainCard
            exerciseContent={buildExplainContent(question)}
            subject={subject}
            questionId={question.id}
            visible
          />
        )}
      </Card>

      {hasBbox && (
        <ManualSplitModal
          assignmentId={assignmentId}
          visible={adjustVisible}
          prefillRegion={{
            question_id: question.id,
            question_number: question.question_number,
            page_index: question.page_index ?? 0,
            x: question.bbox_x!,
            y: question.bbox_y!,
            w: question.bbox_w!,
            h: question.bbox_h!,
          }}
          onSuccess={() => {
            setAdjustVisible(false);
            queryClient.invalidateQueries({ queryKey: ["assignment"] });
          }}
          onCancel={() => setAdjustVisible(false)}
        />
      )}

      <ManualSplitModal
        assignmentId={assignmentId}
        visible={insertVisible}
        insertAfter={{
          question_id: question.id,
          question_number: question.question_number,
          page_index: question.page_index ?? 0,
        }}
        onSuccess={() => {
          setInsertVisible(false);
          queryClient.invalidateQueries({ queryKey: ["assignment"] });
          queryClient.invalidateQueries({ queryKey: ["assignments"] });
        }}
        onCancel={() => setInsertVisible(false)}
      />
    </>
  );
}
