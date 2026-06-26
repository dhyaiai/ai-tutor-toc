import { Card, Tag, Button, Space, Typography, Popconfirm, Popover, Image, Descriptions, Input, message, Spin } from "antd";
import { ReloadOutlined, CheckOutlined, DeleteOutlined, ExpandOutlined } from "@ant-design/icons";
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

export default function QuestionCard({ question, assignmentId, assignmentStatus }: Props) {
  const { reanalyze, reanalyzing } = useReanalysis();
  const queryClient = useQueryClient();
  const statusCfg = QUESTION_STATUS_MAP[question.status] || { color: "default", label: question.status };
  const [adjustVisible, setAdjustVisible] = useState(false);
  const [remarkOpen, setRemarkOpen] = useState(false);
  const [remark, setRemark] = useState("");

  const confirmMutation = useMutation({
    mutationFn: (params: { score?: number; analysis_detail?: string }) =>
      questionService.confirm(question.id, params),
    onSuccess: () => {
      // 同步刷新作业详情、记录列表、学情分析
      queryClient.invalidateQueries({ queryKey: ["assignment"] });
      queryClient.invalidateQueries({ queryKey: ["assignments"] });
      queryClient.invalidateQueries({ queryKey: ["analytics"] });
    },
  });

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
            <Popconfirm
              title="确认此题分析结果？"
              onConfirm={() =>
                confirmMutation.mutate({
                  score: question.score ?? undefined,
                  analysis_detail: question.analysis_detail ?? undefined,
                })
              }
            >
              <Button size="small" icon={<CheckOutlined />} loading={confirmMutation.isPending}>
                确认
              </Button>
            </Popconfirm>
            <Popover
              open={remarkOpen}
              onOpenChange={(open) => {
                // 正在重新生成时不关闭
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
                        placeholder="告诉AI哪里识别有问题，例如：学生答案是手写的，字迹较潦草"
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
