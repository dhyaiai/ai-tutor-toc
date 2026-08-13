/**
 * 作文批改页面（redesign 版本）
 *
 * 结构：
 * - 顶部：页面标题 + 说明（语文 / 英语 两个子板块标签页，科目固定互不混杂）
 * - 每个子板块：
 *   - 上传 Hero 卡片：左侧大拖拽上传区（支持多文件合并），右侧作文信息表单 + 提交
 *   - 记录区：批改记录卡片网格（分数徽章 + 题目 + 操作），点击卡片进入详情侧滑面板
 *
 * 设计要点（redesign 后）：
 * - 上传从一行 inline 表单改为拖拽 Hero + 表单卡片，视觉层次清晰
 * - 记录从通用 Table 改为卡片网格，每篇作文配语义色分数徽章（绿/琥珀/红）
 * - 详情从 Modal 长列表改为双栏 Drawer（原文页面图片 + 批改结果），见 DetailDrawer.tsx
 */

import { useState, useCallback, useEffect, useRef } from "react";
import {
  Card, Form, Select, Upload, Button, Tag, Typography, Tooltip,
  message, Space, Empty, Popconfirm, Input, Progress, Row, Col, Pagination, Segmented, Spin,
} from "antd";
import {
  InboxOutlined, DeleteOutlined, FileTextOutlined,
  ArrowUpOutlined, ArrowDownOutlined, FileOutlined,
  EyeOutlined, EditOutlined, ReadOutlined, GlobalOutlined,
  ClockCircleOutlined,
} from "@ant-design/icons";
import {
  compositionService,
  type CompositionListItem,
} from "../../services/compositionService";
import {
  GRADE_OPTIONS, toSelectOptions,
} from "../../utils/filterConfig";
import { formatDate } from "../../utils/helpers";
import CompositionDetailDrawer from "./DetailDrawer";

const { Title, Text, Paragraph } = Typography;
const { Dragger } = Upload;

/** 每页记录数（卡片网格） */
const PAGE_SIZE = 12;

/** 轮询分页大小（后端 page_size 上限 100）：逐页拉取全量，确保任何一页
 * 中处于批改中的记录都能被轮询合并到状态（见轮询 effect 注释） */
const POLL_PAGE_SIZE = 100;

/** 按得分率映射语义色：≥0.7 优秀绿 / ≥0.5 中等琥珀 / 其余待提升红 */
function rateColor(rate: number): string {
  if (rate >= 0.7) return "#52c41a";
  if (rate >= 0.5) return "#faad14";
  return "#ff4d4f";
}

/** 记录是否处于批改中（pending/correcting） */
const isCorrecting = (status?: string) => status === "pending" || status === "correcting";

/**
 * 分数徽章：圆形，得分率着色，展示 "score / full"。
 * 用于记录卡片左上角，让分数成为每张卡片的第一视觉焦点。
 * 批改中显示 Spin，失败显示灰色"失败"（无分数可展示）。
 */
function ScoreBadge({ score, full, status }: { score: number; full: number; status?: string }) {
  const rate = full > 0 ? score / full : 0;
  const color = rateColor(rate);
  const pending = isCorrecting(status);
  const failed = status === "failed";
  const muted = pending || failed;
  return (
    <div
      style={{
        width: 58,
        height: 58,
        borderRadius: "50%",
        background: muted ? "#fafafa" : `${color}14`,
        border: muted ? "1.5px dashed #d9d9d9" : `1.5px solid ${color}`,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      {pending ? (
        <Spin size="small" />
      ) : failed ? (
        <Text strong style={{ color: "#8c8c8c", fontSize: 14, lineHeight: 1.2 }}>
          失败
        </Text>
      ) : (
        <>
          <Text strong style={{ color, fontSize: 17, lineHeight: 1.1, fontVariantNumeric: "tabular-nums" }}>
            {score}
          </Text>
          <Text style={{ color, fontSize: 10, lineHeight: 1.4, fontVariantNumeric: "tabular-nums" }}>
            /{full}
          </Text>
        </>
      )}
    </div>
  );
}

/**
 * 单个科目的作文批改子板块（语文 / 英语）
 *
 * 科目由父层通过 props 固定传入，上传时不再让用户选择科目，
 * 记录列表也只查询该科目的数据，记录区不再有科目筛选。
 */
function SubjectSection({
  subject,
  onViewDetail,
  active,
}: {
  subject: string;
  onViewDetail: (id: number) => void;
  /** 所在子板块是否激活：两个板块常驻挂载（隐藏而非卸载），未激活时跳过记录查询 */
  active?: boolean;
}) {
  const [form] = Form.useForm();
  const [files, setFiles] = useState<File[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  /** 重新批改中记录的 ID 集合 */
  const [reCorrectingIds, setReCorrectingIds] = useState<Set<number>>(new Set());

  // 用 ref 避免闭包中使用到过期的 files state
  const filesRef = useRef<File[]>([]);
  /** 上传区锚点：空状态引导按钮滚动到此处 */
  const uploadRef = useRef<HTMLDivElement>(null);

  /** 历史记录 */
  const [records, setRecords] = useState<CompositionListItem[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  /** 年级筛选（科目已固定为本板块科目，无需再筛） */
  const [gradeFilter, setGradeFilter] = useState("");
  /** 记录总数（卡片网格分页用） */
  const [total, setTotal] = useState(0);
  /** 卡片网格当前页 */
  const [page, setPage] = useState(1);
  /** 列表请求代次：筛选/翻页/轮询并发时只采纳最新一次请求的结果，避免旧响应覆盖新数据 */
  const loadEpochRef = useRef(0);

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

  /** 合并新文件到列表（按文件名+大小去重） */
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

  /** 上移文件（调整合并顺序） */
  const moveUp = (index: number) => {
    if (index === 0) return;
    setFiles((prev) => {
      const next = [...prev];
      [next[index - 1], next[index]] = [next[index], next[index - 1]];
      filesRef.current = next;
      return next;
    });
  };

  /** 下移文件（调整合并顺序） */
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

  /** 加载历史记录（仅查询本板块科目）；
   *  silent=true 时不显示加载骨架屏（轮询/后台刷新用，避免列表每 5s 闪烁成空白卡片）；
   *  pageNo 缺省时加载当前 page（后端分页，total 为真实总数） */
  const loadRecords = useCallback(async (silent = false, pageNo?: number) => {
    const epoch = ++loadEpochRef.current;
    if (!silent) setRecordsLoading(true);
    try {
      const params: { subject?: string; grade?: string; page?: number; page_size?: number } = {
        subject,
        page: pageNo ?? page,
        page_size: PAGE_SIZE,
      };
      if (gradeFilter) params.grade = gradeFilter;
      const data = await compositionService.list(params);
      if (epoch !== loadEpochRef.current) return; // 已有更新的请求，丢弃本次过期结果
      setRecords(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      if (epoch !== loadEpochRef.current) return;
      console.error("加载历史记录失败:", err);
      message.error("加载历史记录失败，请刷新重试");
    } finally {
      if (epoch === loadEpochRef.current && !silent) setRecordsLoading(false);
    }
  }, [subject, gradeFilter, page]);

  // 仅在所在子板块激活时加载历史记录；首次切换到该板块时自动拉取
  useEffect(() => {
    if (active) loadRecords();
  }, [loadRecords, active]);

  /** 提交批改（科目固定为本板块科目；批改异步执行，提交即返回） */
  const handleSubmit = useCallback(async () => {
    // 防重复提交：antd Button 的 loading 不阻止 onClick，双击会创建两条批改记录
    if (loading) return;
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
          subject, // 固定科目，无需用户选择
          grade: vals.grade,
          title: vals.title,
          essay_type: undefined,
        },
        setUploadProgress,
      );
      // 接口只上传文件+建记录并立即返回，批改在后台执行，上传通道不阻塞
      message.success("已提交，AI 批改中...");
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
  }, [form, loadRecords, subject, loading]);

  /** 是否存在批改中的记录（驱动轮询） */
  const hasPendingRecords = records.some((r) => isCorrecting(r.status));

  // 有批改中的记录时每 5s 轮询（分页拉取全量）：
  // - 只把返回记录的最新状态合并进本地列表（不改动当前分页位置，避免列表跳动）
  // - 全量无批改中记录后停止轮询，并刷新当前页显示最终状态
  // 注意必须轮询全量而非只查第 1 页：非第 1 页的记录被"重新批改"后，
  // 若只查第 1 页，该记录状态永远合不到，且第 1 页无 pending 会提前停止
  // 轮询 → 该记录永久停在"批改中"（死锁，只能刷新页面恢复）
  useEffect(() => {
    if (!hasPendingRecords) return;
    let stopped = false;
    const poll = async () => {
      try {
        // 分页拉取全量（后端 page_size 上限 100，逐页请求直到拉完 total）
        const all: CompositionListItem[] = [];
        let pageNo = 1;
        while (true) {
          const data = await compositionService.list({
            subject,
            grade: gradeFilter || undefined,
            page: pageNo,
            page_size: POLL_PAGE_SIZE,
          });
          all.push(...(data.items || []));
          if (all.length >= (data.total || 0) || !data.items?.length) break;
          pageNo += 1;
        }
        if (stopped) return;
        // 合并最新状态：只更新返回 id 对应的记录，不替换整个列表
        const byId = new Map(all.map((r) => [r.id, r]));
        setRecords((prev) =>
          prev.map((r) => (byId.has(r.id) ? { ...r, ...byId.get(r.id)! } : r)),
        );
        const hasPending = all.some((r) => isCorrecting(r.status));
        if (!hasPending) {
          // 全部完成/失败：停止轮询并刷新当前页
          clearInterval(timer);
          loadRecords(true);
        }
      } catch {
        // 网络抖动：忽略本轮，下一轮重试
      }
    };
    const timer = setInterval(poll, 5000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [hasPendingRecords, subject, gradeFilter, loadRecords]);

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

  /** 重新批改（异步执行，提交即返回，卡片显示批改中状态） */
  const reCorrect = useCallback(async (id: number) => {
    setReCorrectingIds((prev) => new Set(prev).add(id));
    try {
      const updated = await compositionService.reCorrect(id);
      message.success("已提交，AI 重新批改中...");
      // 用后端返回的重置后记录局部更新该卡片，避免整列表重拉导致闪烁；
      // 批改完成状态由 hasPendingRecords 驱动的 5s 静默轮询接管
      setRecords((prev) =>
        prev.map((r) => (r.id === updated.id ? { ...r, ...updated } : r)),
      );
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
  }, []);

  // 当前页兜底：记录被删除后 page 可能越界，按真实总数 total clamp 到有效范围
  const safePage = Math.max(1, Math.min(page, Math.max(1, Math.ceil(total / PAGE_SIZE))));

  return (
    <div>
      {/* ============ 上传 Hero 卡片 ============ */}
      <Card
        ref={uploadRef}
        style={{
          marginBottom: 32,
          borderRadius: 16,
          border: "none",
          boxShadow: "0 6px 24px rgba(22, 119, 255, 0.08)",
        }}
      >
        <Row gutter={[24, 16]}>
          {/* 左：拖拽上传区（常驻，可继续添加） */}
          <Col xs={24} lg={15}>
            <Dragger
              accept=".pdf,.docx,.doc,.txt,.png,.jpg,.jpeg,.webp"
              multiple
              showUploadList={false}
              disabled={loading}
              beforeUpload={(f) => {
                if (f.size > 20 * 1024 * 1024) {
                  message.error(`"${f.name}" 超过 20MB 限制`);
                  return Upload.LIST_IGNORE;
                }
                // beforeUpload 的参数就是原始 File（RcFile），直接加入列表。
                // 注意：不能从 onChange 的 info.file.originFileObj 取文件——
                // antd 在 beforeUpload 返回 false 时会把 info.file 重新 clone 成
                // 裸 File 对象（仅带 uid，不含 originFileObj），该字段恒为
                // undefined，导致选文件后无任何反应（见 D4）
                mergeFiles([f]);
                return false; // 阻止自动上传，由 handleSubmit 统一提交
              }}
              style={{ padding: "8px 0", borderRadius: 12 }}
            >
              <InboxOutlined style={{ fontSize: 40, color: "#1677ff" }} />
              <p style={{ fontSize: 15, margin: "8px 0 4px", color: "#262626" }}>
                点击或拖拽作文文件到此处
              </p>
              <p style={{ fontSize: 12, color: "#8c8c8c", margin: 0 }}>
                支持 PDF / Word / TXT / 图片，单文件不超过 20MB
              </p>
            </Dragger>

            {/* 已选文件列表（支持排序调整合并顺序） */}
            {files.length > 0 && (
              <div style={{ marginTop: 10 }}>
                {files.map((f, index) => (
                  <div
                    key={`${f.name}_${f.size}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      border: "1px solid #f0f0f0",
                      borderRadius: 8,
                      padding: "6px 10px",
                      marginBottom: 6,
                      background: "#fafbfc",
                    }}
                  >
                    <Text type="secondary" style={{ fontSize: 12, width: 18, textAlign: "center", flexShrink: 0 }}>
                      {index + 1}
                    </Text>
                    <FileOutlined style={{ color: "#1677ff", flexShrink: 0 }} />
                    <Text ellipsis style={{ fontSize: 13, flex: 1, minWidth: 0 }} title={f.name}>
                      {f.name}
                    </Text>
                    <Text type="secondary" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                      {formatSize(f.size)}
                    </Text>
                    <Space size={0} style={{ flexShrink: 0 }}>
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
                  </div>
                ))}
                {files.length > 1 && (
                  <Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 2 }}>
                    文件将按列表顺序从上到下合并为一个文档后统一批改
                  </Text>
                )}
              </div>
            )}
          </Col>

          {/* 右：作文信息表单 */}
          <Col xs={24} lg={9}>
            <Form form={form} layout="vertical" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
              <Form.Item
                name="title"
                label="作文题目"
                rules={[{ required: true, message: "请输入作文题目" }]}
              >
                <Input placeholder="如：我的理想" prefix={<EditOutlined style={{ color: "#bfbfbf" }} />} />
              </Form.Item>
              <Form.Item
                name="grade"
                label="年级"
                rules={[{ required: true, message: "请选择年级" }]}
              >
                <Select
                  placeholder="选择年级"
                  options={toSelectOptions(GRADE_OPTIONS)}
                />
              </Form.Item>
              <Form.Item style={{ marginBottom: 8 }}>
                <Button
                  type="primary"
                  size="large"
                  block
                  loading={loading}
                  onClick={handleSubmit}
                  disabled={files.length === 0}
                  icon={<InboxOutlined />}
                >
                  开始批改
                </Button>
              </Form.Item>
              {/* 上传进度 */}
              {loading && (
                <div>
                  <Progress
                    percent={uploadProgress}
                    size="small"
                    status="active"
                    format={(p) => (p === 100 ? "上传完成，AI 批改中..." : `上传中 ${p}%`)}
                  />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    AI 批改通常需要 1~3 分钟，请勿关闭页面
                  </Text>
                </div>
              )}
            </Form>
          </Col>
        </Row>
      </Card>

      {/* ============ 记录区：卡片网格 ============ */}
      <div style={{ marginBottom: 16 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 12,
            marginBottom: 16,
          }}
        >
          <Title level={4} style={{ margin: 0 }}>
            {subject}批改记录
            <Text type="secondary" style={{ fontSize: 13, fontWeight: 400, marginLeft: 8 }}>
              共 {total} 篇
            </Text>
          </Title>
          <Select
            placeholder="筛选年级"
            allowClear
            style={{ width: 140 }}
            value={gradeFilter || undefined}
            onChange={(v) => { setGradeFilter(v || ""); setPage(1); }}
            options={toSelectOptions(GRADE_OPTIONS)}
          />
        </div>

        {recordsLoading ? (
          <Row gutter={[16, 16]}>
            {Array.from({ length: 6 }).map((_, i) => (
              <Col xs={24} sm={12} lg={8} xl={6} key={i}>
                <Card style={{ borderRadius: 12, height: 180 }} loading />
              </Col>
            ))}
          </Row>
        ) : records.length === 0 ? (
          /* 空状态：引导上传 */
          <Card style={{ borderRadius: 12, border: "1px dashed #d9d9d9", background: "#fafbfc" }}>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <span>
                  还没有 {subject} 批改记录
                  <br />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    上传第一篇作文，批改在后台进行，可连续上传多篇
                  </Text>
                </span>
              }
            >
              <Button
                type="primary"
                icon={<InboxOutlined />}
                onClick={() => uploadRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
              >
                上传作文
              </Button>
            </Empty>
          </Card>
        ) : (
          <>
            <Row gutter={[16, 16]}>
              {records.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE).map((r) => {
                return (
                  <Col xs={24} sm={12} lg={8} xl={6} key={r.id}>
                    <Card
                      hoverable
                      onClick={() => onViewDetail(r.id)}
                      style={{ borderRadius: 12, height: "100%" }}
                      styles={{ body: { padding: 16, display: "flex", flexDirection: "column", height: "100%" } }}
                    >
                      {/* 顶部：分数徽章 + 年级/状态 */}
                      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, marginBottom: 10 }}>
                        <ScoreBadge score={r.total_score} full={r.full_score} status={r.status} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Text
                            strong
                            style={{ fontSize: 14, lineHeight: 1.5, cursor: "pointer" }}
                            ellipsis={{ tooltip: r.title }}
                          >
                            {r.title}
                          </Text>
                          <div style={{ marginTop: 6 }}>
                            <Space size={4} wrap>
                              {r.grade && <Tag color={subject === "语文" ? "blue" : "green"} style={{ marginRight: 0 }}>{r.grade}</Tag>}
                              {r.essay_type && <Tag style={{ marginRight: 0 }}>{r.essay_type}</Tag>}
                              <Tag icon={<ClockCircleOutlined />} style={{ marginRight: 0 }} color="default">
                                {r.create_time ? formatDate(r.create_time, true) : "-"}
                              </Tag>
                              {isCorrecting(r.status) && (
                                <Tag color="processing" icon={<Spin size="small" />} style={{ marginRight: 0 }}>
                                  批改中
                                </Tag>
                              )}
                              {r.status === "failed" && (
                                <Tooltip title={r.error_message || "批改失败，可点击重新批改"}>
                                  <Tag color="error" style={{ marginRight: 0 }}>批改失败</Tag>
                                </Tooltip>
                              )}
                            </Space>
                          </div>
                        </div>
                      </div>

                      {/* 底部：操作 */}
                      <div style={{ marginTop: "auto", paddingTop: 8 }}>
                        <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                          <Button
                            type="link"
                            size="small"
                            icon={<EyeOutlined />}
                            onClick={(e) => { e.stopPropagation(); onViewDetail(r.id); }}
                          >
                            批改结果
                          </Button>
                          <Popconfirm
                            title="确认重新批改？将重新识别分值并评分。"
                            onConfirm={(e) => { e?.stopPropagation(); reCorrect(r.id); }}
                          >
                            <Button
                              type="link"
                              size="small"
                              loading={reCorrectingIds.has(r.id)}
                              disabled={isCorrecting(r.status)}
                              style={{ color: "#faad14" }}
                              onClick={(e) => e.stopPropagation()}
                            >
                              重新批改
                            </Button>
                          </Popconfirm>
                          <Button
                            type="link"
                            size="small"
                            icon={<FileTextOutlined />}
                            onClick={(e) => { e.stopPropagation(); viewOriginal(r.id, r.title); }}
                          >
                            原文
                          </Button>
                          <Popconfirm
                            title="确认删除？"
                            onConfirm={(e) => { e?.stopPropagation(); deleteRecord(r.id); }}
                          >
                            <Button
                              type="link"
                              size="small"
                              danger
                              icon={<DeleteOutlined />}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </Popconfirm>
                        </div>
                      </div>
                    </Card>
                  </Col>
                );
              })}
            </Row>
            <div style={{ display: "flex", justifyContent: "center", marginTop: 20 }}>
              <Pagination
                current={safePage}
                pageSize={PAGE_SIZE}
                total={total}
                onChange={setPage}
                showSizeChanger={false}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** 科目切换键：chinese=语文 / english=英语 */
type SubjectKey = "chinese" | "english";

/** 作文批改主页：语文 / 英语 两个子板块 */
export default function CompositionPage() {
  /** 详情侧滑面板（父层共享，两个子板块都可打开） */
  const [detailId, setDetailId] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  /** 当前科目（默认语文），用大号分段切换器控制 */
  const [subject, setSubject] = useState<SubjectKey>("chinese");

  /** 打开详情面板 */
  const viewDetail = useCallback((id: number) => {
    setDetailId(id);
    setDetailOpen(true);
  }, []);

  return (
    <div style={{ padding: "12px 0 24px", maxWidth: 1280, margin: "0 auto" }}>
      {/* 页面头部：无 emoji，纯排版层次 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ marginBottom: 0, letterSpacing: "-0.02em" }}>
          作文批改
        </Title>
      </div>

      {/* 语文 / 英语 大号分段切换：激活项深蓝实底白字（样式见 soft-ui.css 8.1 节） */}
      <Segmented
        block
        className="soft-section-switcher"
        value={subject}
        onChange={(v) => setSubject(v as SubjectKey)}
        options={[
          { value: "chinese", label: "语文", icon: <ReadOutlined /> },
          { value: "english", label: "英语", icon: <GlobalOutlined /> },
        ]}
      />

      {/* 两个子板块常驻挂载（隐藏而非卸载），切换时保留各自的表单与记录状态；
           active 传给板块控制记录加载，未激活的板块不发查询请求 */}
      <div hidden={subject !== "chinese"} style={{ marginTop: 24 }}>
        <SubjectSection subject="语文" active={subject === "chinese"} onViewDetail={viewDetail} />
      </div>
      <div hidden={subject !== "english"} style={{ marginTop: 24 }}>
        <SubjectSection subject="英语" active={subject === "english"} onViewDetail={viewDetail} />
      </div>

      {/* 详情侧滑面板 */}
      <CompositionDetailDrawer
        id={detailId}
        open={detailOpen}
        onClose={() => { setDetailOpen(false); setDetailId(null); }}
      />
    </div>
  );
}
