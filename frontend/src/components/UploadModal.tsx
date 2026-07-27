import { useState, useRef, useCallback } from "react";
import {
  Modal, Form, Input, Select, Upload, Button, message, Progress,
  Space, Typography, List,
} from "antd";
import {
  InboxOutlined, ArrowUpOutlined, ArrowDownOutlined,
  DeleteOutlined, FileOutlined, PlusOutlined,
} from "@ant-design/icons";
import { useUpload } from "../hooks/useUpload";
import { GRADE_OPTIONS, SUBJECT_OPTIONS, SEMESTER_OPTIONS, toSelectOptions } from "../utils/filterConfig";

const { Dragger } = Upload;

/** 从 axios 错误或 FastAPI 校验错误中提取可读消息 */
function extractErrorMsg(err: any): string {
  const detail = err?.response?.data?.detail;
  if (!detail) return err?.message || "未知错误";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d.msg || JSON.stringify(d)).join("; ");
  }
  return JSON.stringify(detail);
}

/** 格式化文件大小 */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onSuccess: (assignmentId: number) => void;
}

export default function UploadModal({ open, onClose, onSuccess }: Props) {
  const [form] = Form.useForm();
  const [files, setFiles] = useState<File[]>([]);
  const [filesUploaded, setFilesUploaded] = useState(false);
  const { startFilesUpload, submitAssignment, uploading, progress, reset } = useUpload();

  // 用 ref 避免 onChange 闭包中使用到过期的 files state
  const filesRef = useRef<File[]>([]);

  /** 从文件名提取默认作业名称（去掉扩展名） */
  const getDefaultName = (filename: string): string => {
    const lastDot = filename.lastIndexOf(".");
    return lastDot > 0 ? filename.substring(0, lastDot) : filename;
  };

  /** 合并新文件到列表并自动上传 */
  const mergeAndUpload = useCallback(
    (newFiles: File[]) => {
      // 去重：按 name + size 组合
      const existingKeys = new Set(
        filesRef.current.map((f) => `${f.name}_${f.size}`),
      );
      const unique = newFiles.filter(
        (f) => !existingKeys.has(`${f.name}_${f.size}`),
      );
      if (unique.length === 0) return;

      const merged = [...filesRef.current, ...unique];
      filesRef.current = merged;
      setFiles([...merged]);
      setFilesUploaded(false);

      if (merged.length > 0) {
        form.setFieldsValue({ name: getDefaultName(merged[0].name) });
      }

      // 自动开始上传所有文件
      startFilesUpload(merged)
        .then(() => setFilesUploaded(true))
        .catch((err) => {
          message.error(`文件上传失败：${extractErrorMsg(err)}`);
        });
    },
    [form, startFilesUpload],
  );

  /** 上移文件 */
  const moveUp = (index: number) => {
    if (index === 0) return;
    setFiles((prev) => {
      const next = [...prev];
      [next[index - 1], next[index]] = [next[index], next[index - 1]];
      filesRef.current = next;
      return next;
    });
    // 顺序改变后需要重新上传
    setFilesUploaded(false);
    startFilesUpload(filesRef.current)
      .then(() => setFilesUploaded(true))
      .catch((err) => {
        message.error(`文件上传失败：${extractErrorMsg(err)}`);
      });
  };

  /** 下移文件 */
  const moveDown = (index: number) => {
    setFiles((prev) => {
      if (index >= prev.length - 1) return prev;
      const next = [...prev];
      [next[index], next[index + 1]] = [next[index + 1], next[index]];
      filesRef.current = next;
      return next;
    });
    setFilesUploaded(false);
    startFilesUpload(filesRef.current)
      .then(() => setFilesUploaded(true))
      .catch((err) => {
        message.error(`文件上传失败：${extractErrorMsg(err)}`);
      });
  };

  /** 删除指定文件 */
  const removeFile = (index: number) => {
    const updated = filesRef.current.filter((_, i) => i !== index);
    filesRef.current = updated;
    setFiles(updated);
    setFilesUploaded(false);
    if (updated.length > 0) {
      startFilesUpload(updated)
        .then(() => setFilesUploaded(true))
        .catch(() => {});
    }
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (!filesUploaded) {
        message.error("请等待文件上传完成");
        return;
      }
      const result = await submitAssignment(values);
      message.success("作业上传成功，请前往详情页启动分析");
      form.resetFields();
      filesRef.current = [];
      setFiles([]);
      setFilesUploaded(false);
      reset();
      onSuccess(result.assignment_id);
    } catch (err: any) {
      if (err?.errorFields) {
        return;
      }
      console.error("Submit error:", err);
      const detail = extractErrorMsg(err);
      message.error(`提交失败：${detail}`);
    }
  };

  const handleClose = () => {
    reset();
    filesRef.current = [];
    setFiles([]);
    setFilesUploaded(false);
    onClose();
  };

  const hasFiles = files.length > 0;
  const hasMultiple = files.length > 1;

  return (
    <Modal
      title="上传作业"
      open={open}
      onCancel={handleClose}
      onOk={handleSubmit}
      confirmLoading={false}
      okText="提交作业"
      cancelText="取消"
      width={600}
      destroyOnClose
      okButtonProps={{ disabled: uploading || (hasFiles && !filesUploaded) }}
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        {/* 文件上传区域 */}
        <Form.Item label="作业文件" style={{ marginBottom: 0 }}>
          {hasFiles ? (
            <div>
              {/* 文件列表 */}
              <List
                size="small"
                dataSource={files}
                renderItem={(f, index) => (
                  <List.Item
                    style={{
                      border: "1px solid #e8e8e8",
                      borderRadius: 6,
                      padding: "8px 12px",
                      marginBottom: 6,
                      background: "#fafafa",
                    }}
                  >
                    {/* 文件信息 */}
                    <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 8 }}>
                      <FileOutlined style={{ color: "#1677ff" }} />
                      <Typography.Text
                        ellipsis
                        style={{ maxWidth: 260, fontSize: 13 }}
                        title={f.name}
                      >
                        {f.name}
                      </Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                        {formatSize(f.size)}
                      </Typography.Text>
                    </div>
                    {/* 操作按钮：多文件时显示排序，单文件不显示 */}
                    <Space size={4}>
                      {hasMultiple && (
                        <>
                          <Button
                            type="text"
                            size="small"
                            icon={<ArrowUpOutlined />}
                            disabled={index === 0}
                            onClick={() => moveUp(index)}
                            title="上移"
                          />
                          <Button
                            type="text"
                            size="small"
                            icon={<ArrowDownOutlined />}
                            disabled={index === files.length - 1}
                            onClick={() => moveDown(index)}
                            title="下移"
                          />
                        </>
                      )}
                      <Button
                        type="text"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={() => removeFile(index)}
                        title="移除"
                      />
                    </Space>
                  </List.Item>
                )}
                style={{ marginBottom: 8 }}
              />
              {/* 继续添加文件 */}
              <Upload
                accept=".pdf,.png,.jpg,.jpeg,.webp"
                multiple
                showUploadList={false}
                beforeUpload={(f) => {
                  if (f.size > 50 * 1024 * 1024) {
                    message.error(`"${f.name}" 超过 50MB 限制`);
                    return Upload.LIST_IGNORE;
                  }
                  return false; // 阻止自动上传
                }}
                onChange={(info) => {
                  const newFiles = info.fileList
                    .map((item: any) => item.originFileObj)
                    .filter(Boolean) as File[];
                  if (newFiles.length > 0) {
                    mergeAndUpload(newFiles);
                  }
                }}
              >
                <Button icon={<PlusOutlined />} size="small" disabled={uploading}>
                  继续添加文件
                </Button>
              </Upload>
              {hasMultiple && (
                <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
                  文件将按列表顺序从上到下合并为一个 PDF，使用 ↑↓ 按钮调整顺序
                </Typography.Text>
              )}
            </div>
          ) : (
            <Dragger
              accept=".pdf,.png,.jpg,.jpeg,.webp"
              multiple
              showUploadList={false}
              beforeUpload={(f) => {
                if (f.size > 50 * 1024 * 1024) {
                  message.error(`"${f.name}" 超过 50MB 限制`);
                  return Upload.LIST_IGNORE;
                }
                return false; // 阻止自动上传，由 onChange 统一处理
              }}
              onChange={(info) => {
                const newFiles = info.fileList
                  .map((item: any) => item.originFileObj)
                  .filter(Boolean) as File[];
                if (newFiles.length > 0) {
                  mergeAndUpload(newFiles);
                }
              }}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p>点击或拖拽文件到此区域</p>
              <p style={{ color: "#999" }}>支持 PDF / 图片（PNG、JPG、WebP），单个文件不超过 50MB</p>
              <p style={{ color: "#999", fontSize: 12 }}>支持同时选择多个文件，按顺序合并为一份作业</p>
            </Dragger>
          )}
        </Form.Item>

        {/* 上传进度 */}
        {uploading && (
          <div style={{ textAlign: "center", padding: "8px 0 16px" }}>
            <Progress percent={progress} status="active" strokeColor="#1677ff" />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              正在上传文件...
            </Typography.Text>
          </div>
        )}

        <Form.Item
          name="name"
          label="作业名称"
          rules={[{ required: true, message: "请输入作业名称" }]}
        >
          <Input placeholder="例如：数学第一单元测试" />
        </Form.Item>

        <Space size="middle">
          <Form.Item
            name="grade"
            label="年级"
            rules={[{ required: true, message: "请选择年级" }]}
          >
            <Select
              placeholder="选择年级"
              style={{ width: 160 }}
              options={toSelectOptions(GRADE_OPTIONS)}
            />
          </Form.Item>

          <Form.Item
            name="subject"
            label="科目"
            rules={[{ required: true, message: "请选择科目" }]}
          >
            <Select
              placeholder="选择科目"
              style={{ width: 160 }}
              options={toSelectOptions(SUBJECT_OPTIONS)}
            />
          </Form.Item>
        </Space>

        <Space size="middle">
          <Form.Item
            name="semester"
            label="学期"
            rules={[{ required: true, message: "请选择学期" }]}
          >
            <Select
              placeholder="选择学期"
              style={{ width: 160 }}
              options={toSelectOptions(SEMESTER_OPTIONS)}
            />
          </Form.Item>

          <Form.Item
            name="usage_month"
            label="使用月份"
            rules={[{ required: true, message: "请输入使用月份" }]}
          >
            <Input placeholder="例如：2026-03" style={{ width: 160 }} />
          </Form.Item>
        </Space>

      </Form>
    </Modal>
  );
}
