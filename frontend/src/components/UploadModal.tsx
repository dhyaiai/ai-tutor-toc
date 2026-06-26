import { useState } from "react";
import { Modal, Form, Input, Select, Upload, Button, message, Progress, Space, Typography } from "antd";
import { InboxOutlined, UploadOutlined } from "@ant-design/icons";
import { useUpload } from "../hooks/useUpload";
import { GRADE_OPTIONS, SUBJECT_OPTIONS, SEMESTER_OPTIONS, toSelectOptions } from "../utils/filterConfig";

const { Dragger } = Upload;

/** 从 axios 错误或 FastAPI 校验错误中提取可读消息 */
function extractErrorMsg(err: any): string {
  const detail = err?.response?.data?.detail;
  if (!detail) return err?.message || "未知错误";
  if (typeof detail === "string") return detail;
  // FastAPI 422: detail 是 { loc, msg, type } 数组
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d.msg || JSON.stringify(d)).join("; ");
  }
  return JSON.stringify(detail);
}

interface Props {
  open: boolean;
  onClose: () => void;
  onSuccess: (assignmentId: number) => void;
}

export default function UploadModal({ open, onClose, onSuccess }: Props) {
  const [form] = Form.useForm();
  const [file, setFile] = useState<File | null>(null);
  const [fileUploaded, setFileUploaded] = useState(false);
  const { startFileUpload, submitAssignment, uploading, progress, reset } = useUpload();

  /** 从文件名提取默认作业名称（去掉扩展名） */
  const getDefaultName = (filename: string): string => {
    const lastDot = filename.lastIndexOf(".");
    return lastDot > 0 ? filename.substring(0, lastDot) : filename;
  };

  const handleFileSelected = async (f: File) => {
    setFile(f);
    setFileUploaded(false);
    // 自动填写作业名称为文件名（去掉扩展名）
    form.setFieldsValue({ name: getDefaultName(f.name) });
    try {
      await startFileUpload(f);
      setFileUploaded(true);
    } catch (err: any) {
      console.error("Upload error:", err);
      const detail = extractErrorMsg(err);
      message.error(`文件上传失败：${detail}`);
      setFile(null);
    }
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (!fileUploaded) {
        message.error("请等待文件上传完成");
        return;
      }
      const result = await submitAssignment(values);
      message.success("作业上传成功，请前往详情页启动分析");
      form.resetFields();
      setFile(null);
      setFileUploaded(false);
      reset();
      onSuccess(result.assignment_id);
    } catch (err: any) {
      if (err?.errorFields) {
        // form validation error, Ant Design will show inline messages
        return;
      }
      console.error("Submit error:", err);
      const detail = extractErrorMsg(err);
      message.error(`提交失败：${detail}`);
    }
  };

  const handleClose = () => {
    reset();
    setFile(null);
    setFileUploaded(false);
    onClose();
  };

  return (
    <Modal
      title="上传作业"
      open={open}
      onCancel={handleClose}
      onOk={handleSubmit}
      confirmLoading={false}
      okText="提交作业"
      cancelText="取消"
      width={560}
      destroyOnClose
      okButtonProps={{ disabled: uploading || (!!file && !fileUploaded) }}
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item label="作业文件" style={{ marginBottom: 0 }}>
          {file ? (
            <div
              style={{
                border: "1px dashed #d9d9d9",
                borderRadius: 8,
                padding: "24px",
                textAlign: "center",
                background: "#fafafa",
              }}
            >
              <Typography.Text strong>{file.name}</Typography.Text>
              <br />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {(file.size / 1024 / 1024).toFixed(1)} MB
              </Typography.Text>
              {!fileUploaded && (
                <Button
                  type="link"
                  danger
                  size="small"
                  onClick={() => {
                    setFile(null);
                    setFileUploaded(false);
                    reset();
                  }}
                  style={{ display: "block", margin: "4px auto 0" }}
                >
                  移除
                </Button>
              )}
            </div>
          ) : (
            <Dragger
              accept=".pdf,.png,.jpg,.jpeg,.webp"
              maxCount={1}
              beforeUpload={(f) => {
                if (f.size > 50 * 1024 * 1024) {
                  message.error("文件大小不能超过 50MB");
                  return false;
                }
                handleFileSelected(f);
                return false; // Prevent auto upload
              }}
              showUploadList={false}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p>点击或拖拽文件到此区域</p>
              <p style={{ color: "#999" }}>支持 PDF / 图片（PNG、JPG、WebP），单个文件不超过 50MB</p>
            </Dragger>
          )}
        </Form.Item>

        {/* Progress bar between file area and form fields */}
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
            name="month"
            label="月份"
            rules={[{ required: true, message: "请输入月份" }]}
          >
            <Input placeholder="例如：2026-03" style={{ width: 160 }} />
          </Form.Item>
        </Space>

      </Form>
    </Modal>
  );
}
