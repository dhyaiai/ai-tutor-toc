/**
 * UploadQuestionModal —— 收藏页"上传试题"弹窗。
 *
 * 表单：试卷文件（支持一次选择多个，最多 10 个，可上移/下移排序——
 * 转录按文件顺序进行，答案页要排在试卷后面）+ 年级/科目/学期/题型
 * （与收藏页筛选项一致），文件项置于表单最上方便于先选文件。
 *
 * 流程：提交 → POST /upload-questions 立即 202 返回 task_id →
 * 每 2s 轮询 GET /upload-questions/{task_id}（mountedRef/epoch/ticking 守卫，
 * 模式参照 useReanalysis）→ completed 回调 onSuccess(entries) 交给收藏页
 * 打开编辑弹窗逐个检查；failed/not_found/10 分钟超时提示错误。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Button, Form, message, Modal, Select, Space, Spin, Typography, Upload,
  type UploadFile,
} from "antd";
import {
  ArrowDownOutlined, ArrowUpOutlined, DeleteOutlined, FileOutlined, InboxOutlined,
} from "@ant-design/icons";
import {
  favoriteService,
  type FavoriteUnion,
} from "../../../services/favoriteService";
import {
  GRADE_OPTIONS, SUBJECT_OPTIONS, SEMESTER_OPTIONS, QUESTION_TYPE_OPTIONS,
  toSelectOptions,
} from "../../../utils/filterConfig";

const { Dragger } = Upload;

/** 轮询间隔（毫秒） */
const POLL_INTERVAL = 2000;
/** 最大轮询时间（毫秒）：转录任务约 1~3 分钟，10 分钟为最坏情况兜底 */
const MAX_POLL_TIME = 10 * 60 * 1000;
/** 允许的扩展名（与后端 _UPLOAD_EXTENSIONS 一致） */
const ACCEPT_EXTS = ".docx,.pdf,.png,.jpg,.jpeg,.webp";
/** 最大文件大小（与后端 MAX_UPLOAD_SIZE_MB 一致） */
const MAX_FILE_SIZE = 50 * 1024 * 1024;
/** 一次最多上传文件数（与后端 _MAX_FILES 一致） */
const MAX_FILE_COUNT = 10;

/** 处理阶段文案：轮询期间展示给用户的状态提示 */
const STAGE_TEXT = ["正在识别题目内容…", "正在整理题目与知识点…"];

interface Props {
  open: boolean;
  onClose: () => void;
  /** 转录完成回调：entries 为收藏条目列表（与 /favorites 结构一致，可直接进编辑弹窗） */
  onSuccess: (entries: FavoriteUnion[]) => void;
}

export default function UploadQuestionModal({ open, onClose, onSuccess }: Props) {
  const [form] = Form.useForm();
  // 受控文件列表（UploadFile 带 uid，支持排序/删除；提交时按列表顺序取原始文件）
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  // 轮询阶段（0=上传中 1=识别中 2=整理中），驱动 STAGE_TEXT 展示
  const [stage, setStage] = useState(0);
  // 轮询守卫（参照 useReanalysis）：卸载停止 + 代次防新旧轮询互踩 + ticking 防重叠
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);
  const pollEpochRef = useRef(0);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // 卸载时停止轮询（A4-1 先例：原实现卸载后 interval 仍持续请求）
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, [stopPolling]);

  // 每次打开弹窗重置表单与文件（Modal destroyOnClose 只销毁内部 DOM，state 在外层）
  useEffect(() => {
    if (open) {
      form.resetFields();
      setFileList([]);
    }
  }, [open, form]);

  const handleClose = () => {
    stopPolling();
    pollEpochRef.current += 1; // 使在途轮询回调立即失效
    setSubmitting(false);
    setFileList([]);
    onClose();
  };

  /** 交换相邻文件顺序（dir=1 下移，-1 上移）：转录按列表顺序进行，把答案文件移到试卷之后 */
  const moveFile = (index: number, dir: 1 | -1) => {
    setFileList((prev) => {
      const next = [...prev];
      const target = index + dir;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  /** 格式化文件大小显示 */
  const formatSize = (size?: number) => {
    const bytes = size ?? 0;
    return bytes > 1024 * 1024
      ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
      : `${Math.max(1, Math.round(bytes / 1024))} KB`;
  };

  /** 从 axios 错误中提取可读消息 */
  const extractErrorMsg = (err: any): string =>
    err?.response?.data?.detail || err?.message || "未知错误";

  /** 提交并轮询转录任务 */
  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (fileList.length === 0) {
        message.error("请选择要上传的试卷文件");
        return;
      }

      setSubmitting(true);
      setStage(1);

      // 1. 创建转录任务（202 立即返回）：按用户排序后的列表顺序提交，
      //    后端按此顺序逐文件转录——试卷排前面、答案排后面，防止答案被当成试题
      const formData = new FormData();
      fileList.forEach((f) => {
        if (f.originFileObj) formData.append("files", f.originFileObj as File);
      });
      formData.append("grade", values.grade);
      formData.append("subject", values.subject);
      formData.append("semester", values.semester);
      formData.append("question_type", values.question_type);

      let taskId: string;
      try {
        const created = await favoriteService.uploadQuestion(formData);
        taskId = created.task_id;
      } catch (err: any) {
        message.error("上传失败: " + extractErrorMsg(err));
        setSubmitting(false);
        return;
      }

      // 2. 轮询任务结果（新代次，旧轮询回调被淘汰）
      stopPolling();
      pollEpochRef.current += 1;
      const epoch = pollEpochRef.current;
      const startTime = Date.now();
      let ticking = false;

      pollTimerRef.current = setInterval(async () => {
        if (ticking) return;
        ticking = true;
        try {
          // 被关闭/新轮询取代或组件已卸载：不再请求与更新状态
          if (epoch !== pollEpochRef.current || !mountedRef.current) return;

          const elapsed = Date.now() - startTime;
          if (elapsed >= MAX_POLL_TIME) {
            stopPolling();
            if (mountedRef.current) {
              setSubmitting(false);
              message.error("转录超时，请稍后在收藏列表查看或重新上传");
            }
            return;
          }

          let result;
          try {
            result = await favoriteService.getUploadResult(taskId);
          } catch {
            // 单次轮询请求失败（网络抖动等）不中断，下一轮继续
            return;
          }
          if (epoch !== pollEpochRef.current || !mountedRef.current) return;

          if (result.status === "completed") {
            stopPolling();
            setSubmitting(false);
            setFileList([]);
            form.resetFields();
            const entries = result.entries ?? [];
            if (entries.length > 0) {
              onSuccess(entries);
            } else {
              message.warning("转录完成，但没有生成有效题目");
              onClose();
            }
            return;
          }
          if (result.status === "failed") {
            stopPolling();
            setSubmitting(false);
            message.error("转录失败: " + (result.error || "未知错误"));
            return;
          }
          if (result.status === "not_found") {
            stopPolling();
            setSubmitting(false);
            message.error("任务不存在或已过期，请重新上传");
            return;
          }
          // pending/processing：推进阶段文案（1=识别中 2=整理中）
          setStage(result.status === "pending" ? 1 : 2);
        } finally {
          ticking = false;
        }
      }, POLL_INTERVAL);
    } catch (err: any) {
      if (err?.errorFields) return; // 表单校验失败，字段内已提示
      message.error("提交失败: " + extractErrorMsg(err));
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="上传试题"
      open={open}
      onCancel={handleClose}
      onOk={handleSubmit}
      okText="开始转录"
      cancelText="取消"
      width={560}
      destroyOnClose
      confirmLoading={submitting}
      okButtonProps={{ disabled: fileList.length === 0 || submitting }}
    >
      <Typography.Paragraph type="secondary" style={{ fontSize: 13, marginTop: 8 }}>
        上传 Word / PDF / 图片试卷，系统将自动转录题目、标注知识点，完成后可在编辑弹窗中检查修改。
      </Typography.Paragraph>

      <Form form={form} layout="vertical">
        {/* 试卷文件置于表单最上面：用户先选文件，再填元数据 */}
        <Form.Item label="试卷文件" required>
          <Dragger
            accept={ACCEPT_EXTS}
            multiple
            maxCount={MAX_FILE_COUNT}
            disabled={submitting}
            fileList={fileList}
            showUploadList={false}
            beforeUpload={(f) => {
              if (f.size > MAX_FILE_SIZE) {
                message.error(`"${f.name}" 超过 50MB 限制`);
                return Upload.LIST_IGNORE;
              }
              return false; // 阻止自动上传，由提交时统一处理
            }}
            onChange={(info) => setFileList(info.fileList)}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p>点击或拖拽试卷文件到此区域</p>
            <p style={{ color: "#999" }}>
              支持 Word（.docx）/ PDF / 图片（PNG、JPG、WebP），可一次上传多个文件（最多 {MAX_FILE_COUNT} 个），单个不超过 50MB
            </p>
          </Dragger>

          {/* 文件列表（自定义渲染以便排序）：上移/下移调整转录顺序，删除移除单个文件 */}
          {fileList.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {fileList.map((f, index) => (
                <div
                  key={f.uid}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "4px 0",
                  }}
                >
                  <Typography.Text type="secondary" style={{ fontSize: 12, width: 20 }}>
                    {index + 1}.
                  </Typography.Text>
                  <FileOutlined style={{ color: "#1677ff" }} />
                  <Typography.Text
                    style={{
                      flex: 1,
                      fontSize: 13,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={f.name}
                  >
                    {f.name}（{formatSize(f.size)}）
                  </Typography.Text>
                  <Button
                    size="small"
                    type="text"
                    icon={<ArrowUpOutlined />}
                    disabled={index === 0 || submitting}
                    onClick={() => moveFile(index, -1)}
                    title="上移（提前转录）"
                  />
                  <Button
                    size="small"
                    type="text"
                    icon={<ArrowDownOutlined />}
                    disabled={index === fileList.length - 1 || submitting}
                    onClick={() => moveFile(index, 1)}
                    title="下移（延后转录）"
                  />
                  <Button
                    size="small"
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    disabled={submitting}
                    onClick={() =>
                      setFileList((prev) => prev.filter((x) => x.uid !== f.uid))
                    }
                    title="移除"
                  />
                </div>
              ))}
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                转录按文件顺序进行，请把试卷排在前面、答案页排在后面。
              </Typography.Text>
            </div>
          )}
        </Form.Item>

        <Form.Item
          name="grade"
          label="年级"
          rules={[{ required: true, message: "请选择年级" }]}
        >
          <Select placeholder="选择年级" options={toSelectOptions(GRADE_OPTIONS)} />
        </Form.Item>

        <Space size="middle" style={{ display: "flex" }}>
          <Form.Item
            name="subject"
            label="科目"
            rules={[{ required: true, message: "请选择科目" }]}
            style={{ flex: 1 }}
          >
            <Select placeholder="选择科目" options={toSelectOptions(SUBJECT_OPTIONS)} />
          </Form.Item>
          <Form.Item
            name="semester"
            label="学期"
            rules={[{ required: true, message: "请选择学期" }]}
            style={{ flex: 1 }}
          >
            <Select placeholder="选择学期" options={toSelectOptions(SEMESTER_OPTIONS)} />
          </Form.Item>
        </Space>

        <Form.Item
          name="question_type"
          label="题型"
          tooltip="多道题的试卷中，AI 会按每道题实际题型转录；无法判断时使用此处选择的值"
          rules={[{ required: true, message: "请选择题型" }]}
        >
          <Select placeholder="选择题型" options={toSelectOptions(QUESTION_TYPE_OPTIONS)} />
        </Form.Item>
      </Form>

      {/* 转录进度：轮询期间展示阶段文案 */}
      {submitting && (
        <Space style={{ display: "flex", justifyContent: "center", marginTop: 4 }}>
          <Spin size="small" />
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {STAGE_TEXT[Math.min(stage, STAGE_TEXT.length - 1)]}
          </Typography.Text>
        </Space>
      )}
    </Modal>
  );
}
