import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Steps, Tag, Typography, Progress, Space, Button, Tooltip, Popconfirm, message } from "antd";
import {
  LoadingOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
  MinusOutlined,
  CloseOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { assignmentService } from "../services/assignmentService";

interface Props {
  assignmentId: number;
  visible: boolean;
  onClose: () => void;
}

const ACTIVE_STATES = new Set(["splitting", "splitted", "grading", "processing"]);

const STEPS = [
  { key: "splitting", title: "OCR 切割" },
  { key: "splitted", title: "切割完成" },
  { key: "grading", title: "AI 评分" },
  { key: "completed", title: "分析完成" },
];

/** 初始位置：左下角，避开右下角 AI 助手按钮；position.y = 面板顶部距视口顶部距离 */
const INITIAL_POSITION = { x: 24, y: Math.max(60, window.innerHeight - 480) };

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`;
}

export default function AnalysisProgressPanel({ assignmentId, visible, onClose }: Props) {
  const queryClient = useQueryClient();
  const [collapsed, setCollapsed] = useState(false);
  const [cancelLoading, setCancelLoading] = useState(false);
  const [startTime] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleCancel = useCallback(async () => {
    setCancelLoading(true);
    try {
      await assignmentService.cancelAnalysis(assignmentId);
      message.success("已终止分析");
      queryClient.invalidateQueries({ queryKey: ["assignment", assignmentId] });
    } catch {
      message.error("终止失败");
    } finally {
      setCancelLoading(false);
    }
  }, [assignmentId, queryClient]);

  // ── 拖动状态 ──
  const [position, setPosition] = useState(INITIAL_POSITION);
  const dragRef = useRef<{
    startMouseX: number;
    startMouseY: number;
    startPosX: number;
    startPosY: number;
  } | null>(null);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      // 只在标题栏触发拖动，避免在按钮上拖动
      const target = e.target as HTMLElement;
      if (target.closest("button")) return;
      dragRef.current = {
        startMouseX: e.clientX,
        startMouseY: e.clientY,
        startPosX: position.x,
        startPosY: position.y,
      };
      e.preventDefault();
    },
    [position],
  );

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const dx = e.clientX - dragRef.current.startMouseX;
      const dy = e.clientY - dragRef.current.startMouseY;
      setPosition({
        // 水平：面板左边缘不超出视口，右边缘至少留 50px 可见
        x: Math.max(0, Math.min(window.innerWidth - 50, dragRef.current.startPosX + dx)),
        // 垂直：标题栏始终在视口内（y ≥ 0），底部至少留 60px 可见
        y: Math.max(0, Math.min(window.innerHeight - 60, dragRef.current.startPosY + dy)),
      });
    };
    const handleMouseUp = () => {
      dragRef.current = null;
    };
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, []);

  // 计时
  useEffect(() => {
    if (visible) {
      timerRef.current = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startTime) / 1000));
      }, 1000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [visible, startTime]);

  // 高频轮询
  const { data } = useQuery({
    queryKey: ["assignment", assignmentId],
    queryFn: () => assignmentService.getDetail(assignmentId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ACTIVE_STATES.has(status) ? 2000 : false;
    },
    enabled: visible && !!assignmentId,
  });

  const currentStep = useMemo(() => {
    if (!data) return 0;
    const idx = STEPS.findIndex((s) => s.key === data.status);
    if (idx >= 0) return idx;
    if (data.status === "completed") return STEPS.length - 1;
    return 0;
  }, [data]);

  const questionProgress = useMemo(() => {
    if (!data?.questions?.length) return { completed: 0, total: 0, percent: 0 };
    const total = data.questions.length;
    const completed = data.questions.filter(
      (q) => q.status === "completed" || q.status === "confirmed",
    ).length;
    return { completed, total, percent: Math.round((completed / total) * 100) };
  }, [data]);

  const isCompleted = data?.status === "completed";
  const isFailed = data?.status === "failed";
  const isRunning = !data || data.status === "pending" || ACTIVE_STATES.has(data.status);

  // 完成后 2 秒自动关闭
  useEffect(() => {
    if (isCompleted) {
      const t = setTimeout(() => onClose(), 2000);
      return () => clearTimeout(t);
    }
  }, [isCompleted, onClose]);

  const canCollapse = isRunning;

  // ── 折叠态渲染 ──
  if (collapsed && isRunning) {
    const stepLabel = STEPS[currentStep]?.title || "运行中";
    const icon = isFailed ? (
      <CloseCircleOutlined style={{ color: "#ff4d4f" }} />
    ) : (
      <LoadingOutlined spin style={{ color: "#1677ff" }} />
    );
    return (
      <div
        onMouseDown={handleMouseDown}
        onClick={() => setCollapsed(false)}
        style={{
          position: "fixed",
          left: position.x,
          top: position.y,
          zIndex: 1000,
          background: "#fff",
          borderRadius: 20,
          boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
          padding: "8px 16px 8px 12px",
          display: "flex",
          alignItems: "center",
          gap: 8,
          cursor: "grab",
          userSelect: "none",
          border: "1px solid #e8e8e8",
        }}
      >
        {icon}
        <span style={{ fontSize: 13, fontWeight: 500, color: "#333" }}>{stepLabel}</span>
        <Tag icon={<ClockCircleOutlined />} color="default" style={{ margin: 0, fontSize: 12 }}>
          {formatElapsed(elapsed)}
        </Tag>
        {questionProgress.total > 0 && (
          <span style={{ fontSize: 12, color: "#999" }}>
            {questionProgress.completed}/{questionProgress.total}
          </span>
        )}
      </div>
    );
  }

  if (!visible) return null;

  // ── 标题栏左侧 ──
  const titleIcon = isCompleted ? (
    <CheckCircleOutlined style={{ color: "#52c41a" }} />
  ) : isFailed ? (
    <CloseCircleOutlined style={{ color: "#ff4d4f" }} />
  ) : (
    <LoadingOutlined spin style={{ color: "#1677ff" }} />
  );

  const titleText = isCompleted ? "分析完成" : isFailed ? "分析失败" : "作业分析进度";

  const statusText = (() => {
    if (!data) return "正在连接...";
    switch (data.status) {
      case "splitting": {
        if (elapsed < 60) return "正在使用 OCR 识别并切割题目...";
        if (elapsed < 300) return "OCR 处理中，大文件或多页作业可能耗时较长，请稍候...";
        return "OCR 处理超时，请检查文件是否清晰可读，或终止后重试";
      }
      case "splitted":
        return `切割完成，共 ${questionProgress.total} 题，即将开始 AI 评分`;
      case "grading":
        return `AI 正在逐题评分（${questionProgress.completed}/${questionProgress.total}）...`;
      case "processing":
        return "分析进行中...";
      case "completed":
        return "分析完成！";
      case "failed":
        return data.ai_summary || "分析失败";
      default:
        return "";
    }
  })();

  return (
    <div
      style={{
        position: "fixed",
        left: position.x,
        top: position.y,
        zIndex: 1000,
        width: 400,
        background: "#fff",
        borderRadius: 12,
        boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
        border: "1px solid #e8e8e8",
        overflow: "hidden",
      }}
    >
      {/* 标题栏（可拖动） */}
      <div
        onMouseDown={handleMouseDown}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: "1px solid #f0f0f0",
          background: isFailed ? "#fff2f0" : isCompleted ? "#f6ffed" : "#fafafa",
          cursor: "move",
          userSelect: "none",
        }}
      >
        <Space size={8}>
          {titleIcon}
          <span style={{ fontSize: 14, fontWeight: 600, color: "#333" }}>{titleText}</span>
        </Space>
        <Space size={4}>
          {isRunning && (
            <Popconfirm
              title="确定终止当前分析？"
              description="已处理的部分不会丢失，可重新开始分析"
              onConfirm={handleCancel}
              okText="终止"
              cancelText="取消"
              okButtonProps={{ danger: true, loading: cancelLoading }}
            >
              <Tooltip title="终止分析">
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<StopOutlined />}
                />
              </Tooltip>
            </Popconfirm>
          )}
          {canCollapse && (
            <Tooltip title="收起">
              <Button
                type="text"
                size="small"
                icon={<MinusOutlined />}
                onClick={() => setCollapsed(true)}
              />
            </Tooltip>
          )}
          <Tooltip title="关闭">
            <Button type="text" size="small" icon={<CloseOutlined />} onClick={onClose} />
          </Tooltip>
        </Space>
      </div>

      {/* 内容区 */}
      <div style={{ padding: "12px 16px 16px" }}>
        <Steps
          current={currentStep}
          size="small"
          direction="vertical"
          style={{ marginBottom: 12 }}
          items={STEPS.map((step, i) => {
            let status: "wait" | "process" | "finish" | "error" = "wait";
            if (isFailed && i === currentStep) status = "error";
            else if (i < currentStep) status = "finish";
            else if (i === currentStep) status = isFailed ? "error" : "process";

            const icon =
              status === "process" && !isFailed ? (
                <LoadingOutlined spin />
              ) : status === "error" ? (
                <CloseCircleOutlined />
              ) : undefined;

            return { title: step.title, status, icon };
          })}
        />

        <Typography.Paragraph
          style={{
            textAlign: "center",
            fontSize: 13,
            marginBottom: 12,
            color: isFailed ? "#ff4d4f" : isCompleted ? "#52c41a" : "#1677ff",
          }}
        >
          {statusText}
        </Typography.Paragraph>

        {isRunning && (
          <div style={{ textAlign: "center", marginBottom: 12 }}>
            <Space size="large">
              <Tag icon={<ClockCircleOutlined />} color="default">
                已用时 {formatElapsed(elapsed)}
              </Tag>
              {questionProgress.total > 0 && (
                <Tag color="processing">
                  {questionProgress.completed}/{questionProgress.total} 题
                </Tag>
              )}
            </Space>
          </div>
        )}

        {questionProgress.total > 0 && isRunning && (
          <Progress
            percent={questionProgress.percent}
            size="small"
            style={{ marginBottom: 12 }}
            format={() => `${questionProgress.completed}/${questionProgress.total} 题已完成`}
          />
        )}

        {isFailed && data.ai_summary && (
          <div
            style={{
              background: "#fff2f0",
              border: "1px solid #ffccc7",
              padding: 10,
              borderRadius: 6,
            }}
          >
            <Typography.Text type="danger" strong>
              <ExclamationCircleOutlined style={{ marginRight: 4 }} />
              错误详情
            </Typography.Text>
            <Typography.Paragraph type="danger" style={{ marginTop: 6, marginBottom: 0, fontSize: 12 }}>
              {data.ai_summary}
            </Typography.Paragraph>
          </div>
        )}

        {isCompleted && (
          <div
            style={{
              background: "#f6ffed",
              border: "1px solid #b7eb8f",
              padding: 10,
              borderRadius: 6,
            }}
          >
            <Typography.Text type="success" strong>
              <CheckCircleOutlined style={{ marginRight: 4 }} />
              分析摘要
            </Typography.Text>
            <Typography.Paragraph style={{ marginTop: 6, marginBottom: 0, fontSize: 12 }}>
              {data?.ai_summary}
            </Typography.Paragraph>
          </div>
        )}
      </div>
    </div>
  );
}
