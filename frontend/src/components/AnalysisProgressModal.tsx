import { useEffect, useMemo, useRef, useState } from "react";
import { Modal, Steps, Tag, Typography, Progress, Space, Button } from "antd";
import {
  LoadingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { assignmentService } from "../services/assignmentService";

interface Props {
  assignmentId: number;
  open: boolean;
  onClose: () => void;
}

/** 活动状态集合 */
const ACTIVE_STATES = new Set(["splitting", "splitted", "grading", "processing"]);

/** 步骤定义 */
const STEPS = [
  { key: "splitting", title: "OCR 切割", icon: LoadingOutlined },
  { key: "splitted", title: "切割完成", icon: CheckCircleOutlined },
  { key: "grading", title: "AI 评分分析", icon: LoadingOutlined },
  { key: "completed", title: "分析完成", icon: CheckCircleOutlined },
];

/** 格式化耗时 */
function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`;
}

export default function AnalysisProgressModal({ assignmentId, open, onClose }: Props) {
  const [startTime] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 计时器
  useEffect(() => {
    if (open) {
      timerRef.current = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startTime) / 1000));
      }, 1000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [open, startTime]);

  // 高频轮询作业详情
  const { data } = useQuery({
    queryKey: ["assignment", assignmentId],
    queryFn: () => assignmentService.getDetail(assignmentId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ACTIVE_STATES.has(status) ? 2000 : false;
    },
    enabled: open && !!assignmentId,
  });

  // 当前步骤索引
  const currentStep = useMemo(() => {
    if (!data) return 0;
    const idx = STEPS.findIndex((s) => s.key === data.status);
    if (idx >= 0) return idx;
    if (data.status === "completed") return STEPS.length - 1;
    return 0;
  }, [data]);

  // 题目完成进度
  const questionProgress = useMemo(() => {
    if (!data?.questions?.length) return { completed: 0, total: 0, percent: 0 };
    const total = data.questions.length;
    const completed = data.questions.filter(
      (q) => q.status === "completed" || q.status === "confirmed"
    ).length;
    return { completed, total, percent: Math.round((completed / total) * 100) };
  }, [data]);

  const isCompleted = data?.status === "completed";
  const isFailed = data?.status === "failed";
  const isRunning = data && ACTIVE_STATES.has(data.status);

  // 完成后延迟关闭
  useEffect(() => {
    if (isCompleted) {
      const timeout = setTimeout(() => onClose(), 2000);
      return () => clearTimeout(timeout);
    }
  }, [isCompleted, onClose]);

  // 构建步骤 items
  const stepItems = useMemo(() => {
    return STEPS.map((step, i) => {
      let status: "wait" | "process" | "finish" | "error" = "wait";
      if (isFailed && i === currentStep) {
        status = "error";
      } else if (i < currentStep) {
        status = "finish";
      } else if (i === currentStep) {
        status = isFailed ? "error" : "process";
      }
      const icon =
        status === "process" && !isFailed ? (
          <LoadingOutlined spin />
        ) : status === "error" ? (
          <CloseCircleOutlined />
        ) : undefined;
      return { title: step.title, status, icon };
    });
  }, [currentStep, isFailed]);

  // 状态描述文案
  const statusText = useMemo(() => {
    if (!data) return "正在连接...";
    switch (data.status) {
      case "splitting":
        return "正在使用 OCR 识别并切割题目...";
      case "splitted":
        return `切割完成，共识别 ${questionProgress.total} 道题目，即将开始 AI 评分`;
      case "grading":
        return `AI 正在逐题评分分析（${questionProgress.completed}/${questionProgress.total}）...`;
      case "processing":
        return "分析进行中...";
      case "completed":
        return "分析完成！";
      case "failed":
        return data.ai_summary || "分析失败，请重试";
      default:
        return "";
    }
  }, [data, questionProgress]);

  return (
    <Modal
      title={
        <Space>
          {isCompleted ? (
            <CheckCircleOutlined style={{ color: "#52c41a" }} />
          ) : isFailed ? (
            <CloseCircleOutlined style={{ color: "#ff4d4f" }} />
          ) : (
            <LoadingOutlined spin style={{ color: "#1677ff" }} />
          )}
          <span>作业分析进度</span>
        </Space>
      }
      open={open}
      onCancel={isRunning ? undefined : onClose}
      footer={
        isFailed
          ? [<Button key="close" onClick={onClose}>关闭</Button>]
          : isRunning
          ? null
          : null
      }
      closable={!isRunning}
      maskClosable={false}
      keyboard={!isRunning}
      width={520}
    >
      {/* 步骤指示器 */}
      <Steps
        current={currentStep}
        size="small"
        direction="vertical"
        style={{ marginBottom: 24 }}
        items={stepItems}
      />

      {/* 状态文本 */}
      <Typography.Paragraph
        style={{
          textAlign: "center",
          fontSize: 14,
          color: isFailed ? "#ff4d4f" : isCompleted ? "#52c41a" : "#1677ff",
        }}
      >
        {statusText}
      </Typography.Paragraph>

      {/* 耗时 & 题目统计 */}
      {isRunning && (
        <div style={{ textAlign: "center", marginBottom: 12 }}>
          <Space size="large">
            <Tag icon={<ClockCircleOutlined />} color="default">
              已用时 {formatElapsed(elapsed)}
            </Tag>
            {questionProgress.total > 0 && (
              <Tag color="processing">
                题目 {questionProgress.completed}/{questionProgress.total}
              </Tag>
            )}
          </Space>
        </div>
      )}

      {/* 题目级进度条 */}
      {questionProgress.total > 0 && isRunning && (
        <Progress
          percent={questionProgress.percent}
          size="small"
          style={{ marginBottom: 8 }}
          format={() => `${questionProgress.completed}/${questionProgress.total} 题已完成`}
        />
      )}

      {/* 错误详情 */}
      {isFailed && data.ai_summary && (
        <div
          style={{
            background: "#fff2f0",
            border: "1px solid #ffccc7",
            padding: 12,
            borderRadius: 6,
            marginTop: 12,
          }}
        >
          <Typography.Text type="danger" strong>
            <ExclamationCircleOutlined style={{ marginRight: 6 }} />
            错误详情
          </Typography.Text>
          <Typography.Paragraph
            type="danger"
            style={{ marginTop: 8, marginBottom: 0, fontSize: 13 }}
          >
            {data.ai_summary}
          </Typography.Paragraph>
        </div>
      )}

      {/* 完成提示 */}
      {isCompleted && data.ai_summary && (
        <div
          style={{
            background: "#f6ffed",
            border: "1px solid #b7eb8f",
            padding: 12,
            borderRadius: 6,
            marginTop: 12,
          }}
        >
          <Typography.Text type="success" strong>
            <CheckCircleOutlined style={{ marginRight: 6 }} />
            分析摘要
          </Typography.Text>
          <Typography.Paragraph
            style={{ marginTop: 8, marginBottom: 0, fontSize: 13 }}
          >
            {data.ai_summary}
          </Typography.Paragraph>
        </div>
      )}
    </Modal>
  );
}
