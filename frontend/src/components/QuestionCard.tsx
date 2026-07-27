import { Card, Tag, Button, Space, Typography, Popconfirm, Popover, Image, Descriptions, Input, message, Spin, Collapse } from "antd";
import { ReloadOutlined, DeleteOutlined, ExpandOutlined } from "@ant-design/icons";
import { useState } from "react";
import { useReanalysis } from "../hooks/useReanalysis";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { questionService } from "../services/questionService";
import { QUESTION_STATUS_MAP } from "../utils/constants";
import { getScoreRate } from "../utils/helpers";
import ManualSplitModal from "./ManualSplitModal";
import type { QuestionItem } from "../services/assignmentService";

interface Props {
  question: QuestionItem;
  assignmentId: number;
  assignmentStatus: string;
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
  const statusCfg = QUESTION_STATUS_MAP[question.status] || { color: "default", label: question.status };
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
          <Tag color={statusCfg.color} style={{ fontSize: 11 }}>{statusCfg.label}</Tag>
          {question.confidence_score != null && question.confidence_score < 0.7 && (
            <Tag color="orange" style={{ fontSize: 11 }}>低置信度</Tag>
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
          {question.student_answer || "-"}
        </Descriptions.Item>
        <Descriptions.Item label="正确答案">
          {question.correct_answer || "-"}
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
                {name}
              </Tag>
            );
          })}
        </div>
      )}
      {question.common_mistakes && Array.isArray(question.common_mistakes) && question.common_mistakes.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>常见错误：</Typography.Text>
          {(question.common_mistakes as string[]).map((m: string, i: number) => (
            <Tag key={i} color="orange" style={{ fontSize: 11, marginTop: 2 }}>{m}</Tag>
          ))}
        </div>
      )}
      {question.analysis_detail && (
        <Typography.Paragraph
          type="secondary"
          style={{ marginTop: 8, fontSize: 12 }}
          ellipsis={{ rows: 2, expandable: true }}
        >
          {question.analysis_detail}
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
export default function QuestionCard({ question, assignmentId, assignmentStatus }: Props) {
  const { reanalyze, reanalyzing } = useReanalysis(assignmentId);
  const queryClient = useQueryClient();
  const statusCfg = QUESTION_STATUS_MAP[question.status] || { color: "default", label: question.status };
  const [adjustVisible, setAdjustVisible] = useState(false);
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
              <Tag color={statusCfg.color}>{statusCfg.label}</Tag>
            </Space>
          }
          extra={
            <Space>
              {showEditTools && (
                <>
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
                      {name}
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
        </Card>

        {hasBbox && (
          <ManualSplitModal
            assignmentId={assignmentId}
            visible={adjustVisible}
            prefillRegion={{
              question_id: question.id,
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
            <Tag color={statusCfg.color}>{statusCfg.label}</Tag>
            {question.confidence_score != null && question.confidence_score < 0.7 && (
              <Tag color="orange">低置信度</Tag>
            )}
          </Space>
        }
        extra={
          <Space>
            {showEditTools && (
              <>
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
                {question.student_answer || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="正确答案">
                {question.correct_answer || "-"}
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
                      {name}
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
                    {m}
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
                {question.analysis_detail}
              </Typography.Paragraph>
            )}
          </div>
        </div>
      </Card>

      {hasBbox && (
        <ManualSplitModal
          assignmentId={assignmentId}
          visible={adjustVisible}
          prefillRegion={{
            question_id: question.id,
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
    </>
  );
}
