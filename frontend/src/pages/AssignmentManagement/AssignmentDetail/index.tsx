import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Card, Typography, Descriptions, Tag, Space, Button, Spin, Alert, message } from "antd";
import { ArrowLeftOutlined, PlayCircleOutlined, ScissorOutlined, EyeOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { assignmentService } from "../../../services/assignmentService";
import { ASSIGNMENT_STATUS_MAP } from "../../../utils/constants";
import { formatDate } from "../../../utils/helpers";
import QuestionCard from "../../../components/QuestionCard";
import ManualSplitModal from "../../../components/ManualSplitModal";
import AnalysisProgressPanel from "../../../components/AnalysisProgressPanel";

/** 需要前端持续轮询的活动状态 */
const ACTIVE_STATES = new Set(["splitting", "splitted", "grading", "processing"]);

export default function AssignmentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [analyzing, setAnalyzing] = useState(false);
  const [showProgress, setShowProgress] = useState(false);
  const [userClosed, setUserClosed] = useState(false);
  const [manualSplitVisible, setManualSplitVisible] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["assignment", id],
    queryFn: () => assignmentService.getDetail(Number(id)),
    // 页面慢速轮询（仅做后台兜底，弹窗有自己的高速轮询）
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
      setShowProgress(true);
      setUserClosed(false);
    } catch (err) {
      console.error("启动分析失败:", err);
      message.error("启动分析失败");
    } finally {
      setAnalyzing(false);
    }
  };

  // 页面加载时如果分析正在运行，自动弹出监控弹窗（用户主动关闭后不再自动弹出）
  useEffect(() => {
    if (data && ACTIVE_STATES.has(data.status) && !showProgress && !userClosed) {
      setShowProgress(true);
    }
    if (data && !ACTIVE_STATES.has(data.status)) {
      setUserClosed(false);
    }
  }, [data, showProgress, userClosed]);

  if (isLoading) return <Spin size="large" style={{ display: "block", margin: "100px auto" }} />;
  if (error || !data) return <Alert type="error" message="加载失败" />;

  const statusCfg = ASSIGNMENT_STATUS_MAP[data.status] || { color: "default", label: data.status };
  // 手动切割：pending/failed 状态可操作，其他状态仅显示（禁用）
  const canManualSplit = data.status === "pending" || data.status === "failed";
  // 开始分析：只要不是 pending（还没切割题目）就能点，支持重新分析
  const canStartAnalysis = data.status !== "pending";
  // 正在分析中时不显示手动切割
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
              <Button
                type={canManualSplit ? "primary" : "default"}
                icon={<ScissorOutlined />}
                disabled={!canManualSplit}
                onClick={() => setManualSplitVisible(true)}
              >
                手动切割
              </Button>
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
            <Tag color={statusCfg.color}>{statusCfg.label}</Tag>
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
            message="AI 整体分析"
            description={data.ai_summary}
            style={{ marginTop: 16 }}
          />
        )}

        {/* 分析失败简要提示（详细错误见弹窗） */}
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

      {/* 悬浮进度面板 */}
      <AnalysisProgressPanel
        assignmentId={Number(id)}
        visible={showProgress}
        onClose={() => {
          setShowProgress(false);
          setUserClosed(true);
          queryClient.invalidateQueries({ queryKey: ["assignment", id] });
        }}
      />

      {/* 手动切割弹窗 */}
      <ManualSplitModal
        assignmentId={Number(id)}
        visible={manualSplitVisible}
        prefillRegion={null}
        onSuccess={() => {
          setManualSplitVisible(false);
          setUserClosed(true);  // 阻止进度面板自动弹出（手动切割无后台任务）
          queryClient.invalidateQueries({ queryKey: ["assignment", id] });
        }}
        onCancel={() => setManualSplitVisible(false)}
      />

    </div>
  );
}
