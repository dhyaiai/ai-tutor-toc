import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Card, Typography, Descriptions, Tag, Space, Button, Spin, Alert, message } from "antd";
import { ArrowLeftOutlined, PlayCircleOutlined, ScissorOutlined, EyeOutlined, UploadOutlined, ReloadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { assignmentService } from "../../../services/assignmentService";
import { ASSIGNMENT_STATUS_MAP } from "../../../utils/constants";
import { formatDate } from "../../../utils/helpers";
import QuestionCard from "../../../components/QuestionCard";
import ManualSplitModal from "../../../components/ManualSplitModal";
import AnswerSplitModal from "../../../components/AnswerSplitModal";

/** 需要前端持续轮询的活动状态 */
const ACTIVE_STATES = new Set(["splitting", "splitted", "grading", "processing"]);

export default function AssignmentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [analyzing, setAnalyzing] = useState(false);
  const [reSummarizing, setReSummarizing] = useState(false);
  const [manualSplitVisible, setManualSplitVisible] = useState(false);
  const [answerSplitVisible, setAnswerSplitVisible] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["assignment", id],
    queryFn: () => assignmentService.getDetail(Number(id)),
    // 分析进行中时定期轮询刷新状态
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ACTIVE_STATES.has(status) ? 10000 : false;
    },
    enabled: !!id,
  });

  const handleStartAnalysis = async () => {
    if (!id) return;
    setAnalyzing(true);
    try {
      await assignmentService.analyze(Number(id));
      message.success("分析已开始，请稍候...");
      queryClient.invalidateQueries({ queryKey: ["assignment", id] });
    } catch (err) {
      console.error("启动分析失败:", err);
      message.error("启动分析失败");
    } finally {
      setAnalyzing(false);
    }
  };

  /** 重新汇总整卷分数和AI评语 */
  const handleReSummarize = async () => {
    if (!id) return;
    setReSummarizing(true);
    try {
      await assignmentService.reSummarize(Number(id));
      message.success("整卷分析已更新");
      queryClient.invalidateQueries({ queryKey: ["assignment", id] });
    } catch (err) {
      console.error("重新汇总失败:", err);
      message.error("重新汇总失败");
    } finally {
      setReSummarizing(false);
    }
  };

  if (isLoading) return <Spin size="large" style={{ display: "block", margin: "100px auto" }} />;
  if (error || !data) return <Alert type="error" message="加载失败" />;

  const statusCfg = ASSIGNMENT_STATUS_MAP[data.status] || { color: "default", label: data.status };
  // 手动切割：pending/failed 状态可操作，其他状态仅显示（禁用）
  const canManualSplit = data.status === "pending" || data.status === "failed";
  // 开始分析：必须先完成手动切割（splitted/completed/failed），未切割（pending）不能分析
  const canStartAnalysis = data.status === "splitted" || data.status === "completed" || data.status === "failed";
  // 正在分析中时不显示手动切割和答案切割
  const showManualSplit = !["grading", "processing"].includes(data.status);

  return (
    <div>
      <Button
        icon={<ArrowLeftOutlined />}
        style={{ marginBottom: 16 }}
        onClick={() => navigate("/assignments/records")}
      >
        返回记录
      </Button>

      <Card style={{ marginBottom: 16 }}>
        <Space align="start" style={{ width: "100%", justifyContent: "space-between" }}>
          <Typography.Title level={4} style={{ margin: 0 }}>{data.name}</Typography.Title>
          <Space>
            {showManualSplit && (
              <>
                <Button
                  type={canManualSplit ? "primary" : "default"}
                  icon={<ScissorOutlined />}
                  disabled={!canManualSplit}
                  onClick={() => setManualSplitVisible(true)}
                >
                  手动切割
                </Button>
                <Button
                  icon={<UploadOutlined />}
                  disabled={!data.questions || data.questions.length === 0}
                  onClick={() => setAnswerSplitVisible(true)}
                >
                  答案切割
                </Button>
              </>
            )}
            {canStartAnalysis && (
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={analyzing}
                onClick={handleStartAnalysis}
              >
                开始分析
              </Button>
            )}
          </Space>
        </Space>
        <Descriptions column={4} size="small" style={{ marginTop: 16 }}>
          <Descriptions.Item label="年级">{data.grade}</Descriptions.Item>
          <Descriptions.Item label="科目">{data.subject}</Descriptions.Item>
          <Descriptions.Item label="学期">{data.semester}</Descriptions.Item>
          <Descriptions.Item label="月份">{data.month}</Descriptions.Item>
          <Descriptions.Item label="得分/总分">
            {data.total_score != null ? `${data.total_score} / ${data.full_total ?? "-"}` : "-"}
          </Descriptions.Item>
          <Descriptions.Item label="状态">
            <Space>
              <Tag color={statusCfg.color}>{statusCfg.label}</Tag>
              {data.status === "completed" && (
                <Button
                  size="small"
                  type="link"
                  icon={<ReloadOutlined />}
                  loading={reSummarizing}
                  onClick={handleReSummarize}
                >
                  重新分析
                </Button>
              )}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="上传时间">{formatDate(data.created_at, true)}</Descriptions.Item>
          <Descriptions.Item label="作业预览">
            <Button
              type="link"
              size="small"
              icon={<EyeOutlined />}
              onClick={() => {
                if (data.file_url) {
                  window.open(data.file_url, "_blank", "noopener");
                } else {
                  message.error("无法加载原卷文件");
                }
              }}
            >
              查看原卷
            </Button>
          </Descriptions.Item>
        </Descriptions>

        {/* 整体分析摘要（完成后显示） */}
        {data.ai_summary && data.status === "completed" && (
          <Alert
            type="info"
            message="助教有话说"
            description={data.ai_summary}
            style={{ marginTop: 16 }}
          />
        )}

        {/* 分析失败提示 */}
        {data.status === "failed" && (
          <Alert
            type="error"
            message="分析失败"
            description={data.ai_summary || "未知错误，请重试"}
            style={{ marginTop: 16 }}
            showIcon
          />
        )}
      </Card>

      {/* 题目列表 —— 切割完成后即可看到 */}
      {data.questions.length > 0 && (
        <>
          <Typography.Title level={5}>
            题目列表（{data.questions.length} 题）
            {ACTIVE_STATES.has(data.status) && (
              <Tag color="processing" style={{ marginLeft: 8 }}>分析中</Tag>
            )}
          </Typography.Title>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {data.questions.map((q) => (
              <QuestionCard
                key={q.id}
                question={q}
                assignmentId={Number(id)}
                assignmentStatus={data.status}
              />
            ))}
          </Space>
        </>
      )}

      {/* 手动切割弹窗 */}
      <ManualSplitModal
        assignmentId={Number(id)}
        visible={manualSplitVisible}
        prefillRegion={null}
        onSuccess={() => {
          setManualSplitVisible(false);
          queryClient.invalidateQueries({ queryKey: ["assignment", id] });
        }}
        onCancel={() => setManualSplitVisible(false)}
      />

      {/* 答案切割弹窗 */}
      <AnswerSplitModal
        assignmentId={Number(id)}
        questions={(data.questions || []).filter((q) => !q.parent_id).map((q) => ({
          id: q.id,
          question_number: q.question_number,
        }))}
        visible={answerSplitVisible}
        onSuccess={() => {
          setAnswerSplitVisible(false);
          queryClient.invalidateQueries({ queryKey: ["assignment", id] });
        }}
        onCancel={() => setAnswerSplitVisible(false)}
      />

    </div>
  );
}
