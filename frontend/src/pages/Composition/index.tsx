/**
 * 作文批改页面
 *
 * 布局：
 * - 上部：上传作文文件（PDF/Word/图片），年级和科目必选，横向排列
 * - 下部：作文记录列表，区分年级/科目筛选，点击进入查看批改详情
 */

import { useState, useCallback, useEffect, useRef } from "react";
import {
  Card, Form, Select, Upload, Button, Table, Tag, Typography,
  message, Modal, Divider, List, Space, Result, Empty, Popconfirm, Input, Spin,
} from "antd";
import {
  InboxOutlined, EyeOutlined, DeleteOutlined, FileTextOutlined,
  CheckCircleOutlined, ArrowUpOutlined, ArrowDownOutlined,
  FileOutlined, PlusOutlined,
} from "@ant-design/icons";
import {
  compositionService,
  type CompositionResult,
  type CompositionListItem,
} from "../../services/compositionService";
import {
  GRADE_OPTIONS, SUBJECT_OPTIONS, toSelectOptions,
} from "../../utils/filterConfig";
import { formatDate } from "../../utils/helpers";

const { Title, Text, Paragraph } = Typography;
const { Dragger } = Upload;

/** 作文科目选项 */
const COMP_SUBJECT_OPTIONS = toSelectOptions(["语文", "英语"]);

/** 作文批改详情弹窗 */
function DetailModal({
  id,
  open,
  onClose,
}: {
  id: number | null;
  open: boolean;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<CompositionResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (id !== null && open) {
      setLoading(true);
      compositionService
        .get(id)
        .then(setDetail)
        .catch(() => message.error("加载批改详情失败"))
        .finally(() => setLoading(false));
    }
  }, [id, open]);

  return (
    <Modal
      title="📝 批改详情"
      open={open}
      onCancel={onClose}
      footer={null}
      width={800}
      destroyOnClose
    >
      {loading ? (
        <div style={{ textAlign: "center", padding: 40 }}><Spin /></div>
      ) : detail ? (
        <div style={{ maxHeight: "60vh", overflow: "auto" }}>
          <Result
            status={detail.total_score / detail.full_score >= 0.7 ? "success" : "info"}
            title={`${detail.total_score} / ${detail.full_score} 分`}
            subTitle={
              <Space>
                <Tag color={detail.subject === "语文" ? "blue" : "green"}>{detail.subject}</Tag>
                {detail.title}
                {detail.grade && <Text type="secondary"> | {detail.grade}</Text>}
              </Space>
            }
          />

          {detail.dimension_scores && (() => {
            const DIM_COLORS = ["#4388eaff", "#1ed127ff", "#faad14", "#eb2f96", "#722ed1", "#13c2c2"];
            const entries = Object.entries(detail.dimension_scores);
            const scored = entries.reduce((sum, [, v]) => sum + Number(v), 0);
            const remaining = Math.max(0, detail.full_score - scored);
            return (
              <>
                <Divider>维度评分</Divider>
                {/* 颜色标识 */}
                <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 20px", marginBottom: 12 }}>
                  {entries.map(([k, v], i) => (
                    <Space key={k} size={6}>
                      <span style={{ display: "inline-block", width: 12, height: 12, borderRadius: 2, background: DIM_COLORS[i % DIM_COLORS.length] }} />
                      <Text>{k}</Text>
                      <Text strong>{v} 分</Text>
                    </Space>
                  ))}
                </div>
                {/* 长方形：总长度为满分，各维度按颜色填充 */}
                <div
                  style={{
                    display: "flex",
                    width: "100%",
                    height: 28,
                    borderRadius: 6,
                    overflow: "hidden",
                    background: "#f0f0f0",
                    border: "1px solid #e8e8e8",
                  }}
                >
                  {entries.map(([k, v], i) => {
                    const pct = (Number(v) / detail.full_score) * 100;
                    return (
                      <div
                        key={k}
                        title={`${k}：${v} 分`}
                        style={{
                          width: `${pct}%`,
                          background: DIM_COLORS[i % DIM_COLORS.length],
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          color: "#fff",
                          fontSize: 12,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                        }}
                      >
                        {pct >= 10 ? `${v}` : ""}
                      </div>
                    );
                  })}
                  {remaining > 0 && (
                    <div style={{ width: `${(remaining / detail.full_score) * 100}%` }} title={`未得分：${remaining} 分`} />
                  )}
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>0</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>满分 {detail.full_score} 分</Text>
                </div>
              </>
            );
          })()}

          {detail.overall_comment && (
            <>
              <Divider>总评</Divider>
              <Paragraph>{detail.overall_comment}</Paragraph>
            </>
          )}

          {detail.revision_suggestions?.length ? (
            <>
              <Divider>逐处修改建议 ({detail.revision_suggestions.length} 处)</Divider>
              <List
                dataSource={detail.revision_suggestions}
                renderItem={(s) => (
                  <List.Item>
                    <div style={{ width: "100%" }}>
                      <Space size={4}>
                        <Tag color="orange">{s.revision_type}</Tag>
                        <Text type="secondary">{s.position}</Text>
                      </Space>
                      <div style={{ marginTop: 4 }}>
                        <Text delete style={{ color: "#ff4d4f" }}>{s.original_text}</Text>
                        <br />
                        <Text style={{ color: "#52c41a" }}>→ {s.revised_text}</Text>
                      </div>
                      <div style={{ marginTop: 2 }}><Text type="secondary">{s.reason}</Text></div>
                    </div>
                  </List.Item>
                )}
              />
            </>
          ) : null}

          {detail.polish_advice && (
            <>
              <Divider>润色建议</Divider>
              <Paragraph>{detail.polish_advice}</Paragraph>
            </>
          )}

          {detail.sample_essay && (
            <>
              <Divider>范文参考</Divider>
              <Paragraph style={{ whiteSpace: "pre-wrap", background: "#f5f5f5", padding: 16, borderRadius: 4 }}>
                {detail.sample_essay}
              </Paragraph>
            </>
          )}

          {/* 作文原文 */}
          {detail.content && (
            <>
              <Divider>作文原文</Divider>
              <Paragraph style={{ whiteSpace: "pre-wrap", background: "#fafafa", padding: 16, borderRadius: 4, maxHeight: 200, overflow: "auto" }}>
                {detail.content}
              </Paragraph>
            </>
          )}
        </div>
      ) : (
        <Empty description="无法加载详情" />
      )}
    </Modal>
  );
}

/** 作文批改主页 */
export default function CompositionPage() {
  const [form] = Form.useForm();
  const [files, setFiles] = useState<File[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  /** 重新批改中记录的 ID 集合 */
  const [reCorrectingIds, setReCorrectingIds] = useState<Set<number>>(new Set());

  // 用 ref 避免闭包中使用到过期的 files state
  const filesRef = useRef<File[]>([]);

  /** 历史记录 */
  const [records, setRecords] = useState<CompositionListItem[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [filters, setFilters] = useState({ grade: "", subject: "" });

  /** 详情弹窗 */
  const [detailId, setDetailId] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);

  /** 从文件名提取默认作文题目（去掉扩展名） */
  const getTitleFromFilename = (filename: string): string => {
    const lastDot = filename.lastIndexOf(".");
    return lastDot > 0 ? filename.substring(0, lastDot) : filename;
  };

  /** 格式化文件大小 */
  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  /** 合并新文件到列表 */
  const mergeFiles = useCallback(
    (newFiles: File[]) => {
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

      // 自动用第一个文件名填充作文题目
      if (merged.length > 0) {
        form.setFieldsValue({ title: getTitleFromFilename(merged[0].name) });
      }
    },
    [form],
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
  };

  /** 删除指定文件 */
  const removeFile = (index: number) => {
    const updated = filesRef.current.filter((_, i) => i !== index);
    filesRef.current = updated;
    setFiles(updated);
  };

  /** 查看原文：跳转新标签页打开原始文件（与作业详情一致） */
  const viewOriginal = useCallback(async (id: number, _title: string) => {
    try {
      const { url } = await compositionService.getFileUrl(id);
      if (url) {
        window.open(url, "_blank", "noopener");
      } else {
        message.error("无法加载原文件");
      }
    } catch {
      message.error("获取文件地址失败");
    }
  }, []);

  /** 加载历史记录 */
  const loadRecords = useCallback(async () => {
    setRecordsLoading(true);
    try {
      const params: { subject?: string; grade?: string } = {};
      if (filters.subject) params.subject = filters.subject;
      if (filters.grade) params.grade = filters.grade;
      const data = await compositionService.list(params);
      setRecords(data.items || []);
    } catch {
      // 静默失败
    } finally {
      setRecordsLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    loadRecords();
  }, [loadRecords]);

  /** 更新筛选项 */
  const updateFilter = (key: string, value: string) => {
    setFilters((f) => ({ ...f, [key]: value }));
  };

  /** 提交批改 */
  const handleSubmit = useCallback(async () => {
    if (filesRef.current.length === 0) {
      message.warning("请选择作文文件");
      return;
    }
    const vals = await form.validateFields().catch(() => null);
    if (!vals) return;
    setLoading(true);
    setUploadProgress(0);
    try {
      await compositionService.correct(
        filesRef.current,
        {
          subject: vals.subject,
          grade: vals.grade,
          title: vals.title,
          essay_type: undefined,
        },
        setUploadProgress,
      );
      message.success("批改完成");
      form.resetFields();
      filesRef.current = [];
      setFiles([]);
      loadRecords();
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || "批改失败";
      message.error(typeof detail === "string" ? detail : "批改失败，请重试");
    } finally {
      setLoading(false);
      setUploadProgress(0);
    }
  }, [form, loadRecords]);

  /** 删除记录 */
  const deleteRecord = useCallback(async (id: number) => {
    try {
      await compositionService.delete(id);
      message.success("已删除");
      loadRecords();
    } catch {
      message.error("删除失败");
    }
  }, [loadRecords]);

  /** 重新批改 */
  const reCorrect = useCallback(async (id: number) => {
    setReCorrectingIds((prev) => new Set(prev).add(id));
    try {
      await compositionService.reCorrect(id);
      message.success("重新批改完成");
      loadRecords();
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || "重新批改失败";
      message.error(typeof detail === "string" ? detail : "重新批改失败，请重试");
    } finally {
      setReCorrectingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }, [loadRecords]);

  /** 查看详情 */
  const viewDetail = useCallback((id: number) => {
    setDetailId(id);
    setDetailOpen(true);
  }, []);

  const columns = [
    {
      title: "作文题目",
      dataIndex: "title",
      key: "title",
      width: 210,
      ellipsis: true,
      render: (v: string, r: CompositionListItem) => (
        <a onClick={() => viewDetail(r.id)} title={v}>{v}</a>
      ),
    },
    {
      title: "查看原文",
      key: "view_original",
      width: 90,
      render: (_: unknown, r: CompositionListItem) => (
        <Button
          type="link"
          size="small"
          icon={<FileTextOutlined />}
          onClick={() => viewOriginal(r.id, r.title)}
        >
          原文
        </Button>
      ),
    },
    {
      title: "科目",
      dataIndex: "subject",
      key: "subject",
      width: 80,
      render: (v: string) => <Tag color={v === "语文" ? "blue" : "green"}>{v}</Tag>,
    },
    {
      title: "年级",
      dataIndex: "grade",
      key: "grade",
      width: 60,
    },
    {
      title: "得分",
      key: "score",
      width: 80,
      render: (_: unknown, r: CompositionListItem) => (
        <Text
          strong
          style={{ color: r.total_score / r.full_score >= 0.7 ? "#52c41a" : "#ff4d4f" }}
        >
          {r.total_score}/{r.full_score}
        </Text>
      ),
    },
    {
      title: "批改时间",
      dataIndex: "create_time",
      key: "create_time",
      width: 100,
      render: (v: string) => formatDate(v, true),
    },
    {
      title: "操作",
      key: "action",
      width: 200,
      render: (_: unknown, r: CompositionListItem) => (
        <Space>
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={() => viewDetail(r.id)}
          >
            批改结果
          </Button>
          <Popconfirm
            title="确认重新批改？将重新识别分值并评分。"
            onConfirm={() => reCorrect(r.id)}
          >
            <Button
              type="link"
              loading={reCorrectingIds.has(r.id)}
              style={{ color: "#faad14" }}
            >
              重新批改
            </Button>
          </Popconfirm>
          <Popconfirm title="确认删除？" onConfirm={() => deleteRecord(r.id)}>
            <Button type="link" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: "24px 0" }}>
      <Title level={3}>📝 作文批改</Title>
      <Paragraph type="secondary">
        上传作文文件（PDF/Word/图片/TXT），AI 自动提取文字并批改，提供维度评分、逐处修改建议和范文参考。
      </Paragraph>

      {/* 上传区域 */}
      <Card style={{ marginBottom: 24 }}>
        <Form
          form={form}
          layout="inline"
          initialValues={{ subject: "语文" }}
          style={{ flexWrap: "wrap", gap: 0 }}
        >
          {/* 文件上传区域 */}
          <Form.Item style={{ minWidth: 260, maxWidth: 500 }}>
            {files.length > 0 ? (
              <div>
                <List
                  size="small"
                  dataSource={files}
                  renderItem={(f, index) => (
                    <List.Item
                      style={{
                        border: "1px solid #e8e8e8",
                        borderRadius: 6,
                        padding: "6px 10px",
                        marginBottom: 4,
                        background: "#fafafa",
                      }}
                    >
                      <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 8 }}>
                        <FileOutlined style={{ color: "#1677ff" }} />
                        <Text ellipsis style={{ maxWidth: 180, fontSize: 13 }} title={f.name}>
                          {f.name}
                        </Text>
                        <Text type="secondary" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                          {formatSize(f.size)}
                        </Text>
                      </div>
                      <Space size={2}>
                        {files.length > 1 && (
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
                  style={{ marginBottom: 6 }}
                />
                <Upload
                  accept=".pdf,.docx,.doc,.txt,.png,.jpg,.jpeg,.webp"
                  multiple
                  showUploadList={false}
                  beforeUpload={(f) => {
                    if (f.size > 20 * 1024 * 1024) {
                      message.error(`"${f.name}" 超过 20MB 限制`);
                      return Upload.LIST_IGNORE;
                    }
                    return false;
                  }}
                  onChange={(info) => {
                    const newFiles = info.fileList
                      .map((item: any) => item.originFileObj)
                      .filter(Boolean) as File[];
                    if (newFiles.length > 0) {
                      mergeFiles(newFiles);
                    }
                  }}
                >
                  <Button icon={<PlusOutlined />} size="small" disabled={loading}>
                    继续添加文件
                  </Button>
                </Upload>
                {files.length > 1 && (
                  <Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 2 }}>
                    文件将按列表顺序从上到下合并为一个 PDF 后统一批改
                  </Text>
                )}
              </div>
            ) : (
              <Upload
                accept=".pdf,.docx,.doc,.txt,.png,.jpg,.jpeg,.webp"
                multiple
                showUploadList={false}
                beforeUpload={(f) => {
                  if (f.size > 20 * 1024 * 1024) {
                    message.error(`"${f.name}" 超过 20MB 限制`);
                    return Upload.LIST_IGNORE;
                  }
                  return false;
                }}
                onChange={(info) => {
                  const newFiles = info.fileList
                    .map((item: any) => item.originFileObj)
                    .filter(Boolean) as File[];
                  if (newFiles.length > 0) {
                    mergeFiles(newFiles);
                  }
                }}
              >
                <Button icon={<InboxOutlined />}>选择作文文件</Button>
              </Upload>
            )}
          </Form.Item>

          <Form.Item
            name="title"
            label="作文题目"
            rules={[{ required: true, message: "请输入作文题目" }]}
          >
            <Input placeholder="如：我的理想" style={{ width: 180 }} />
          </Form.Item>

          <Form.Item
            name="grade"
            label="年级"
            rules={[{ required: true, message: "请选择年级" }]}
          >
            <Select
              placeholder="选择年级"
              style={{ width: 140 }}
              options={toSelectOptions(GRADE_OPTIONS)}
            />
          </Form.Item>

          <Form.Item
            name="subject"
            label="科目"
            rules={[{ required: true, message: "请选择科目" }]}
          >
            <Select
              style={{ width: 100 }}
              options={COMP_SUBJECT_OPTIONS}
            />
          </Form.Item>

          <Form.Item>
            <Button
              type="primary"
              loading={loading}
              onClick={handleSubmit}
              disabled={files.length === 0}
              icon={<CheckCircleOutlined />}
            >
              提交批改
            </Button>
          </Form.Item>
        </Form>
        {/* 上传进度 */}
        {loading && uploadProgress > 0 && (
          <div style={{ textAlign: "center", padding: "8px 0 0" }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              正在上传文件 {uploadProgress}%...
            </Text>
          </div>
        )}
      </Card>

      {/* 记录列表 */}
      <Card title="📋 作文批改记录">
        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            placeholder="筛选科目"
            allowClear
            style={{ width: 120 }}
            value={filters.subject || undefined}
            onChange={(v) => updateFilter("subject", v || "")}
            options={COMP_SUBJECT_OPTIONS}
          />
          <Select
            placeholder="筛选年级"
            allowClear
            style={{ width: 140 }}
            value={filters.grade || undefined}
            onChange={(v) => updateFilter("grade", v || "")}
            options={toSelectOptions(GRADE_OPTIONS)}
          />
        </Space>
        <Table
          columns={columns}
          dataSource={records}
          rowKey="id"
          loading={recordsLoading}
          pagination={{
            pageSize: 10,
            showTotal: (total) => `共 ${total} 条`,
          }}
          locale={{ emptyText: <Empty description="暂无作文批改记录" /> }}
        />
      </Card>

      {/* 详情弹窗 */}
      <DetailModal
        id={detailId}
        open={detailOpen}
        onClose={() => { setDetailOpen(false); setDetailId(null); }}
      />

    </div>
  );
}
