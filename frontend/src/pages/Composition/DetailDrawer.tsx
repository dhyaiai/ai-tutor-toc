/**
 * 作文批改详情侧滑面板
 *
 * 布局：左右双栏
 * - 左栏：作文原文页面图片（后端将 PDF/图片/Word 统一渲染为图片），点击可放大预览
 * - 右栏：AI 批改结果（总分、维度评分、总评、逐处修改建议、润色建议、范文参考）
 *
 * 设计要点（redesign 后）：
 * - 分数用大号数字 + 语义色（绿/琥珀/红）突出展示，数字启用 tabular-nums 对齐
 * - 维度评分用单一主色（#1677ff）的细条形图，替换原版 6 色彩虹
 * - 修改建议做成"原文 → 改后"对照卡片，便于快速浏览
 */

import { useEffect, useState } from "react";
import {
  Drawer, Spin, Typography, Tag, Button, Image, Empty, Space, message, Result,
} from "antd";
import {
  FileTextOutlined, BulbOutlined, BookOutlined, EditOutlined,
} from "@ant-design/icons";
import {
  compositionService,
  type CompositionResult,
} from "../../services/compositionService";
import { formatDate } from "../../utils/helpers";

const { Title, Text, Paragraph } = Typography;

/** 按得分率映射语义色：≥0.7 优秀绿 / ≥0.5 中等琥珀 / 其余待提升红 */
function rateColor(rate: number): string {
  if (rate >= 0.7) return "#52c41a";
  if (rate >= 0.5) return "#faad14";
  return "#ff4d4f";
}

/** 得分率对应的等级文案 */
function rateLabel(rate: number): string {
  if (rate >= 0.85) return "优秀";
  if (rate >= 0.7) return "良好";
  if (rate >= 0.5) return "中等";
  return "待提升";
}

/** 修改建议类型 → Tag 颜色（antd 预置色板，同一类型保持稳定） */
const REVISION_TYPE_COLORS: Record<string, string> = {
  错别字: "red",
  语法: "purple",
  用词: "geekblue",
  句式: "cyan",
  逻辑: "orange",
  内容: "gold",
  结构: "magenta",
  其他: "default",
};

/** 记录是否处于批改中（pending/correcting） */
const isCorrecting = (status?: string) => status === "pending" || status === "correcting";

/**
 * 作文批改详情侧滑面板
 * @param id 批改记录 ID（null 时不加载）
 * @param open 是否打开
 * @param onClose 关闭回调
 */
export default function CompositionDetailDrawer({
  id,
  open,
  onClose,
}: {
  id: number | null;
  open: boolean;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<CompositionResult | null>(null);
  /** 作文原文页面图片（base64 data URL 列表） */
  const [pages, setPages] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  // 打开时并行加载批改详情 + 原文页面图片；
  // 记录还在批改中（pending/correcting）时每 5s 轮询详情，直到完成/失败自动停止
  useEffect(() => {
    if (id === null || !open) return;
    setLoading(true);
    setDetail(null);
    setPages([]);
    let cancelled = false;
    let timer: number | undefined;

    /** 拉取一次详情+原文图；返回 true 表示已到终态（completed/failed） */
    const loadOnce = async (): Promise<boolean> => {
      try {
        const [d, imgs] = await Promise.all([
          compositionService.get(id),
          compositionService.getPageImages(id).catch(() => ({ pages: [], total: 0 })),
        ]);
        if (cancelled) return true;
        setDetail(d);
        setPages(imgs.pages || []);
        return d.status === "completed" || d.status === "failed";
      } catch {
        if (!cancelled) message.error("加载批改详情失败");
        return true;
      }
    };

    (async () => {
      const done = await loadOnce();
      if (cancelled) return;
      setLoading(false);
      if (!done) {
        timer = window.setInterval(async () => {
          if (cancelled) return;
          const finished = await loadOnce();
          if (finished && !cancelled && timer !== undefined) {
            window.clearInterval(timer);
          }
        }, 5000);
      }
    })();

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [id, open]);

  /** 查看原始上传文件（新标签页打开） */
  const viewOriginal = async () => {
    if (!detail) return;
    try {
      const { url } = await compositionService.getFileUrl(detail.id);
      if (url) window.open(url, "_blank", "noopener");
      else message.error("无法加载原文件");
    } catch {
      message.error("获取文件地址失败");
    }
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={1000}
      destroyOnClose
      styles={{ body: { padding: 0, display: "flex", overflow: "hidden" } }}
    >
      {loading ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin size="large" tip="加载批改详情..." />
        </div>
      ) : detail && isCorrecting(detail.status) ? (
        // 批改中：显示加载状态，详情自动轮询刷新
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin size="large" tip="AI 批改中，请稍候...">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              style={{ marginTop: 80 }}
              description="批改完成后将自动刷新"
            />
          </Spin>
        </div>
      ) : detail && detail.status === "failed" ? (
        // 批改失败：展示失败原因，可回列表重新批改
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
          <Result
            status="error"
            title="批改失败"
            subTitle={
              <span>
                失败原因：{detail.error_message || "AI 服务异常，请稍后重试"}
                <br />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  可关闭面板后点击记录卡片上的"重新批改"再次尝试
                </Text>
              </span>
            }
          />
        </div>
      ) : detail ? (
        <>
          {/* ============ 左栏：作文原文 ============ */}
          <div
            style={{
              width: 380,
              flexShrink: 0,
              overflow: "auto",
              borderRight: "1px solid #f0f0f0",
              background: "#fafafa",
              padding: 16,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: 12,
              }}
            >
              <Text strong style={{ fontSize: 15 }}>作文原文</Text>
              {detail.pdf_url && (
                <Button size="small" icon={<FileTextOutlined />} onClick={viewOriginal}>
                  查看原文件
                </Button>
              )}
            </div>

            {/* 页面图片（优先） */}
            {pages.length > 0 ? (
              pages.map((p, i) => (
                <Image
                  key={i}
                  src={p}
                  alt={`作文原文第 ${i + 1} 页`}
                  width="100%"
                  style={{ borderRadius: 8, boxShadow: "0 2px 8px rgba(0,0,0,0.08)", marginBottom: 12 }}
                  preview={{ mask: "点击预览" }}
                />
              ))
            ) : detail.content ? (
              // 无页面图片时降级展示识别出的文本
              <Paragraph
                style={{ whiteSpace: "pre-wrap", lineHeight: 1.9, fontSize: 14 }}
              >
                {detail.content}
              </Paragraph>
            ) : (
              <Empty description="暂无原文内容" />
            )}
          </div>

          {/* ============ 右栏：批改结果 ============ */}
          <div style={{ flex: 1, overflow: "auto", padding: "24px 28px" }}>
            {/* 分数总览 */}
            <div style={{ display: "flex", alignItems: "center", gap: 20, marginBottom: 28 }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6, flexShrink: 0 }}>
                <Text
                  style={{
                    fontSize: 56,
                    fontWeight: 700,
                    lineHeight: 1,
                    letterSpacing: "-0.03em",
                    fontVariantNumeric: "tabular-nums",
                    color: rateColor(detail.full_score > 0 ? detail.total_score / detail.full_score : 0),
                  }}
                >
                  {detail.total_score}
                </Text>
                <Text type="secondary" style={{ fontSize: 20, fontVariantNumeric: "tabular-nums" }}>
                  / {detail.full_score}
                </Text>
              </div>
              <div style={{ minWidth: 0 }}>
                <Space size={6} style={{ marginBottom: 4 }}>
                  <Tag
                    color={rateColor(detail.full_score > 0 ? detail.total_score / detail.full_score : 0)}
                    style={{ marginRight: 0 }}
                  >
                    {rateLabel(detail.full_score > 0 ? detail.total_score / detail.full_score : 0)}
                  </Tag>
                  <Tag color={detail.subject === "语文" ? "blue" : "green"} style={{ marginRight: 0 }}>
                    {detail.subject}
                  </Tag>
                  {detail.strict_level > 0 && (
                    <Tag style={{ marginRight: 0 }}>严格度 Lv.{detail.strict_level}</Tag>
                  )}
                </Space>
                <div>
                  <Text strong style={{ fontSize: 16 }} ellipsis={{ tooltip: detail.title }}>
                    {detail.title}
                  </Text>
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {detail.grade ? `${detail.grade} · ` : ""}
                  {detail.essay_type ? `${detail.essay_type} · ` : ""}
                  {detail.create_time ? formatDate(detail.create_time, true) : ""}
                </Text>
              </div>
            </div>

            {/* 维度评分：单一主色细条形图 */}
            {detail.dimension_scores && Object.keys(detail.dimension_scores).length > 0 && (
              <div style={{ marginBottom: 28 }}>
                <Title level={5} style={{ marginBottom: 14 }}>维度评分</Title>
                <div
                  style={{
                    background: "#fafbfc",
                    border: "1px solid #f0f0f0",
                    borderRadius: 12,
                    padding: "16px 18px",
                  }}
                >
                  {(() => {
                    // 维度得分条使用各维度中的最高分归一化（而非总分），
                    // 使各维度之间的相对高低一目了然
                    const scores = Object.values(detail.dimension_scores).map(Number);
                    const maxDimScore = scores.length > 0 ? Math.max(...scores) : 1;
                    return Object.entries(detail.dimension_scores).map(([k, v]) => {
                      const pct = maxDimScore > 0 ? (Number(v) / maxDimScore) * 100 : 0;
                      return (
                        <div key={k} style={{ marginBottom: 14 }}>
                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "baseline",
                              marginBottom: 6,
                            }}
                          >
                            <Text style={{ fontSize: 13 }}>{k}</Text>
                            <Text strong style={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
                              {Number(v)} 分
                            </Text>
                          </div>
                          <div
                            style={{
                              height: 8,
                              borderRadius: 4,
                              background: "#f0f2f5",
                              overflow: "hidden",
                            }}
                          >
                            <div
                              style={{
                                width: `${Math.min(100, pct)}%`,
                                height: "100%",
                                borderRadius: 4,
                                background: "#1677ff",
                                transition: "width 0.6s ease",
                              }}
                            />
                          </div>
                        </div>
                      );
                    });
                  })()}
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    满分 {detail.full_score} 分 · 维度得分之间相对比较
                  </Text>
                </div>
              </div>
            )}

            {/* 总评 */}
            {detail.overall_comment && (
              <div
                style={{
                  marginBottom: 28,
                  borderLeft: "3px solid #1677ff",
                  background: "#f6f9ff",
                  borderRadius: "0 12px 12px 0",
                  padding: "14px 18px",
                }}
              >
                <Title level={5} style={{ marginBottom: 8 }}>总评</Title>
                <Paragraph style={{ marginBottom: 0, lineHeight: 1.9 }}>
                  {detail.overall_comment}
                </Paragraph>
              </div>
            )}

            {/* 逐处修改建议 */}
            {detail.revision_suggestions && detail.revision_suggestions.length > 0 && (
              <div style={{ marginBottom: 28 }}>
                <Title level={5} style={{ marginBottom: 14 }}>
                  逐处修改建议
                  <Text type="secondary" style={{ fontSize: 12, fontWeight: 400, marginLeft: 8 }}>
                    {detail.revision_suggestions.length} 处
                  </Text>
                </Title>
                {detail.revision_suggestions.map((s, i) => (
                  <div
                    key={i}
                    style={{
                      border: "1px solid #f0f0f0",
                      borderRadius: 12,
                      padding: "14px 16px",
                      marginBottom: 10,
                      background: "#fff",
                      boxShadow: "0 1px 4px rgba(0,0,0,0.03)",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                      <Tag
                        color={REVISION_TYPE_COLORS[s.revision_type] || "default"}
                        style={{ marginRight: 0 }}
                      >
                        {s.revision_type || "修改"}
                      </Tag>
                      <Text type="secondary" style={{ fontSize: 12 }}>{s.position}</Text>
                    </div>
                    {/* 原文（红）与改后（绿）对照 */}
                    <div
                      style={{
                        background: "#fff1f0",
                        borderRadius: 8,
                        padding: "8px 12px",
                        marginBottom: 8,
                      }}
                    >
                      <Text type="secondary" style={{ fontSize: 11 }}>原文</Text>
                      <div>
                        <Text delete style={{ color: "#ff4d4f" }}>{s.original_text}</Text>
                      </div>
                    </div>
                    <div
                      style={{
                        background: "#f6ffed",
                        borderRadius: 8,
                        padding: "8px 12px",
                        marginBottom: 8,
                      }}
                    >
                      <Text type="secondary" style={{ fontSize: 11 }}>改为</Text>
                      <div>
                        <Text style={{ color: "#52c41a" }}>{s.revised_text}</Text>
                      </div>
                    </div>
                    {s.reason && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        <BulbOutlined style={{ marginRight: 4, color: "#faad14" }} />
                        {s.reason}
                      </Text>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* 润色建议 */}
            {detail.polish_advice && (
              <div
                style={{
                  marginBottom: 28,
                  background: "#fafbfc",
                  border: "1px solid #f0f0f0",
                  borderRadius: 12,
                  padding: "14px 18px",
                }}
              >
                <Title level={5} style={{ marginBottom: 8 }}>
                  <EditOutlined style={{ color: "#1677ff", marginRight: 8 }} />
                  润色建议
                </Title>
                <Paragraph style={{ marginBottom: 0, lineHeight: 1.9 }}>{detail.polish_advice}</Paragraph>
              </div>
            )}

            {/* 范文参考 */}
            {detail.sample_essay && (
              <div
                style={{
                  marginBottom: 8,
                  background: "#fffbef",
                  border: "1px solid #fff1cc",
                  borderRadius: 12,
                  padding: "14px 18px",
                }}
              >
                <Title level={5} style={{ marginBottom: 8 }}>
                  <BookOutlined style={{ color: "#faad14", marginRight: 8 }} />
                  范文参考
                </Title>
                {(() => {
                  const paragraphs = detail.sample_essay.split("\n").filter((p) => p.trim());
                  return paragraphs.map((para, idx) =>
                    idx === 0 ? (
                      <div key={idx} style={{ textAlign: "center", fontWeight: 600, marginBottom: 10, fontSize: 15 }}>
                        {para}
                      </div>
                    ) : (
                      <Paragraph key={idx} style={{ textIndent: "2em", lineHeight: 1.9, marginBottom: 10 }}>
                        {para}
                      </Paragraph>
                    )
                  );
                })()}
              </div>
            )}
          </div>
        </>
      ) : (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Empty description="无法加载详情" />
        </div>
      )}
    </Drawer>
  );
}
