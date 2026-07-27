/**
 * 子板块3：知识点热力图
 *
 * 功能：
 * - 按年级、科目筛选，并可勾选需要的作业
 * - 三列表格：知识点名称、考察频次、得分率
 * - 得分率越低背景色越红，越高越白（热力图效果）
 * - 支持导出 Excel
 *
 * 注意：
 * - 作业下拉仅显示"已完成"状态的作业，因为热力图需要题目分析数据
 * - 如果筛选后无可选作业，请确认有已完成分析的作业匹配当前年级/科目
 */

import { useState, useMemo, useEffect } from "react";
import {
  Card, Select, Space, Typography, Spin, Table, Empty, Button, message, Row, Col,
} from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import {
  analyticsService,
  type KnowledgeHeatmapResponse,
  type KnowledgeHeatmapItem,
} from "../../services/analyticsService";
import { assignmentService, type AssignmentListItem } from "../../services/assignmentService";
import { GRADE_OPTIONS, SUBJECT_OPTIONS, toSelectOptions } from "../../utils/filterConfig";
import { exportToExcel, type ExportColumn } from "../../utils/exportExcel";

/** Excel 导出列定义 */
const EXPORT_COLUMNS: ExportColumn[] = [
  { key: "knowledge_point", title: "知识点" },
  { key: "frequency", title: "考察频次" },
  { key: "score_rate", title: "得分率" },
];

/**
 * 根据得分率计算热力图单元格背景色。
 * 得分率越低越红，越高越白，使用 RGB 线性插值：
 *   rate=0  → rgb(255, 0, 0)   纯红
 *   rate=0.5 → rgb(255, 128, 128) 粉红
 *   rate=1  → rgb(255, 255, 255) 纯白
 */
function getHeatColor(rate: number): string {
  // 限制在 0~1 范围
  const clamped = Math.max(0, Math.min(1, rate));
  const g = Math.round(clamped * 255);
  const b = Math.round(clamped * 255);
  return `rgb(255, ${g}, ${b})`;
}

/** 根据背景色深浅返回合适的文字颜色（深色背景用白字，浅色背景用深字） */
function getTextColor(rate: number): string {
  return rate < 0.5 ? "#ffffff" : "#333333";
}

export default function KnowledgeHeatmapPanel() {
  /** 筛选条件（科目默认选中数学） */
  const [grade, setGrade] = useState<string>("");
  const [subject, setSubject] = useState<string>("数学");
  const [selectedIds, setSelectedIds] = useState<number[]>([]);

  /**
   * 查询作业列表（供多选下拉框使用）。
   * 始终加载，当 grade 或 subject 变化时自动刷新。
   * 筛选条件可选，不选则展示该用户全部已完成作业。
   */
  const {
    data: assignmentList,
    isLoading: loadingAssignments,
    error: assignmentError,
  } = useQuery({
    queryKey: ["analytics", "assignments-for-heatmap", grade, subject],
    queryFn: async () => {
      const res = await assignmentService.list({
        page_size: 100,  // 后端最大限制 100，超过会返回 422 错误
        ...(grade && { grade }),
        ...(subject && { subject }),
      });
      // 按创建时间降序，最新的排前面；只保留已完成状态的作业（热力图依赖分析数据）
      const items = [...res.items]
        .filter((a: AssignmentListItem) => a.status === "completed")
        .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
      return items;
    },
  });

  /** 获取热力图数据 */
  const {
    data,
    isLoading: loadingHeatmap,
    error: heatmapError,
  } = useQuery<KnowledgeHeatmapResponse>({
    queryKey: ["analytics", "knowledge-heatmap", grade, subject, selectedIds],
    queryFn: () =>
      analyticsService.getKnowledgeHeatmap({
        ...(grade && { grade }),
        ...(subject && { subject }),
        ...(selectedIds.length > 0 && { assignment_ids: selectedIds }),
      }),
  });

  const items = data?.items || [];

  /** 查询失败时打印错误到控制台，方便排查 */
  useEffect(() => {
    if (assignmentError) {
      console.error("知识点热力图: 获取作业列表失败", assignmentError);
    }
  }, [assignmentError]);

  useEffect(() => {
    if (heatmapError) {
      console.error("知识点热力图: 获取热力图数据失败", heatmapError);
    }
  }, [heatmapError]);

  /** 表格列定义 */
  const columns = [
    {
      title: "知识点",
      dataIndex: "knowledge_point",
      key: "knowledge_point",
      width: 200,
    },
    {
      title: "考察频次",
      dataIndex: "frequency",
      key: "frequency",
      width: 120,
      align: "right" as const,
      sorter: (a: KnowledgeHeatmapItem, b: KnowledgeHeatmapItem) => a.frequency - b.frequency,
      render: (val: number) => (
        <Typography.Text strong>{val}</Typography.Text>
      ),
    },
    {
      title: "得分率",
      dataIndex: "score_rate",
      key: "score_rate",
      width: 120,
      align: "center" as const,
      sorter: (a: KnowledgeHeatmapItem, b: KnowledgeHeatmapItem) => a.score_rate - b.score_rate,
      /** 动态设置热力图背景色 */
      onCell: (record: KnowledgeHeatmapItem) => ({
        style: {
          backgroundColor: getHeatColor(record.score_rate),
          color: getTextColor(record.score_rate),
          fontWeight: 600,
          transition: "background-color 0.2s",
        },
      }),
      render: (val: number) => `${(val * 100).toFixed(1)}%`,
    },
  ];

  /** 作业多选选项（仅已完成作业） */
  const assignmentOptions = useMemo(
    () =>
      (assignmentList || []).map((a: AssignmentListItem) => ({
        label: `${a.name} (${a.grade}·${a.subject}·${a.semester})`,
        value: a.id,
      })),
    [assignmentList],
  );

  /** 导出 Excel（含热力图背景色，异步生成文件） */
  const handleExport = async () => {
    if (items.length === 0) {
      message.warning("暂无数据可导出");
      return;
    }
    const exportData = items.map((item) => ({
      ...item,
      score_rate: `${(item.score_rate * 100).toFixed(1)}%`,
    }));
    await exportToExcel(
      EXPORT_COLUMNS,
      exportData as unknown as Record<string, unknown>[],
      "知识点热力图",
      {
        /** 得分率列：使用与网页一致的红→白渐变背景色 */
        cellStyles: {
          score_rate: (_val, _row, _colIdx, rowIdx) => {
            // exportData 与 items 顺序一致，直接索引取原始得分率（O(1) 替代 find O(n)）
            const originalItem = items[rowIdx];
            if (!originalItem) return {};
            const rate = originalItem.score_rate;
            // 计算热力图背景色：得分率越低越红，越高越白
            const clamped = Math.max(0, Math.min(1, rate));
            const g = Math.round(clamped * 255);
            const b = Math.round(clamped * 255);
            const bgRgb = `FF${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
            const textRgb = rate < 0.5 ? "FFFFFF" : "333333";
            return {
              fill: { fgColor: { rgb: bgRgb }, patternType: "solid" },
              font: { color: { rgb: textRgb }, bold: true },
              alignment: { horizontal: "center" },
            };
          },
        },
      },
    );
    message.success("导出成功");
  };

  /** 筛选条件变化时清空已选作业 */
  const handleGradeChange = (v: string) => {
    setGrade(v);
    setSelectedIds([]);
  };
  const handleSubjectChange = (v: string) => {
    setSubject(v);
    setSelectedIds([]);
  };

  return (
    <div>
      {/* ===== 筛选器 ===== */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space size="middle" wrap style={{ width: "100%" }}>
          <Typography.Text strong>数据筛选：</Typography.Text>
          <Select
            placeholder="年级"
            allowClear
            style={{ width: 130 }}
            value={grade || undefined}
            onChange={(v) => handleGradeChange(v ?? "")}
            options={toSelectOptions(GRADE_OPTIONS)}
          />
          <Select
            placeholder="科目"
            allowClear
            style={{ width: 130 }}
            value={subject || undefined}
            onChange={(v) => handleSubjectChange(v ?? "")}
            options={toSelectOptions(SUBJECT_OPTIONS)}
          />
          <span style={{ marginLeft: 8, color: "#888" }}>
            （选择年级和科目可缩小范围，不选则显示全部已完成作业）
          </span>
        </Space>
      </Card>

      {/* ===== 作业多选 ===== */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={16} align="middle">
          <Col>
            <Typography.Text strong style={{ whiteSpace: "nowrap" }}>
              选择作业：
            </Typography.Text>
          </Col>
          <Col flex={1}>
            {assignmentError && (
              <Typography.Text type="danger" style={{ display: "block", marginBottom: 8 }}>
                加载作业列表失败：{(assignmentError as Error)?.message || "未知错误"}
              </Typography.Text>
            )}
            <Select
              mode="multiple"
              placeholder={
                loadingAssignments
                  ? "加载中..."
                  : "勾选需要分析知识点的作业（不选则显示全部已完成作业）"
              }
              style={{ width: "100%", minWidth: 300 }}
              value={selectedIds}
              onChange={setSelectedIds}
              options={assignmentOptions}
              allowClear
              maxTagCount="responsive"
              optionFilterProp="label"
              loading={loadingAssignments}
              disabled={!!assignmentError}
              notFoundContent={
                loadingAssignments ? (
                  <Spin size="small" />
                ) : (
                  <Empty
                    description={
                      grade || subject
                        ? `没有找到${grade || ""}${subject || ""}已完成分析的作业`
                        : "暂无已完成分析的作业数据，请先完成作业分析"
                    }
                  />
                )
              }
            />
          </Col>
        </Row>
      </Card>

      {/* ===== 热力图表格 ===== */}
      {heatmapError ? (
        <Card>
          <Empty description="加载热力图数据失败，请检查网络或刷新页面重试" />
        </Card>
      ) : loadingHeatmap ? (
        <div style={{ textAlign: "center", padding: 60 }}>
          <Spin size="large" tip="正在计算知识点数据..." />
        </div>
      ) : items.length > 0 ? (
        <Card
          title={
            <Space>
              <span>知识点热力图</span>
              <Typography.Text type="secondary" style={{ fontWeight: "normal", fontSize: 13 }}>
                （共 {items.length} 个知识点，得分率越低背景越红）
              </Typography.Text>
            </Space>
          }
          size="small"
          extra={
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              size="small"
              onClick={handleExport}
            >
              导出热力图
            </Button>
          }
        >
          {/* 图例说明 */}
          <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 12, color: "#888" }}>低得分率</span>
            <span
              style={{
                display: "inline-block",
                width: 120,
                height: 14,
                borderRadius: 2,
                background: "linear-gradient(to right, rgb(255,0,0), rgb(255,128,128), rgb(255,255,255))",
              }}
            />
            <span style={{ fontSize: 12, color: "#888" }}>高得分率</span>
          </div>

          <Table
            columns={columns}
            dataSource={items.map((item, idx) => ({ key: idx, ...item }))}
            pagination={{ pageSize: 50, showSizeChanger: true, showTotal: (t) => `共 ${t} 个知识点` }}
            size="middle"
            bordered
          />
        </Card>
      ) : (
        <Card>
          <Empty
            description={
              grade || subject
                ? "暂无知识点数据，请确认选择了正确的筛选条件或勾选了已完成分析的作业"
                : "请先选择年级和科目筛选，或直接勾选作业，再查看知识点热力图"
            }
          />
        </Card>
      )}
    </div>
  );
}
