/**
 * 子板块2：学生学情看板
 *
 * 功能：
 * - 按年级、科目、学期筛选
 * - 上方折线图：按时间排序的作业得分率变化曲线
 * - 下方表格：作业名称、上传时间、年级、学期、科目、得分率
 * - 支持导出 Excel
 * - 图表和表格随筛选条件联动更新
 *
 * 图表库：@ant-design/charts v2.6（底层 G2 v5），API 与 v1.x G2Plot 不同：
 * - smooth: true → shape: "smooth"（G2 v5 样式通道）
 * - yAxis/xAxis → axis: { y: {...}, x: {...} }（G2 v5 轴配置）
 * - tooltip.formatter → tooltip.items 函数数组
 * - label.content 模板 → label.text + label.formatter
 * - label.offset → offsetY（G2 v5 偏移量）
 */

import { useState, useMemo } from "react";
import {
  Card, Select, Space, Typography, Spin, Table, Empty, Button, message,
} from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Line } from "@ant-design/charts";
import { analyticsService, type DashboardResponse } from "../../services/analyticsService";
import {
  GRADE_OPTIONS,
  SUBJECT_OPTIONS,
  SEMESTER_OPTIONS,
  toSelectOptions,
} from "../../utils/filterConfig";
import { formatDate } from "../../utils/helpers";
import { exportToExcel, type ExportColumn } from "../../utils/exportExcel";

/** Excel 导出列定义 */
const EXPORT_COLUMNS: ExportColumn[] = [
  { key: "name", title: "作业名称" },
  { key: "created_at", title: "上传时间" },
  { key: "grade", title: "年级" },
  { key: "semester", title: "学期" },
  { key: "subject", title: "科目" },
  { key: "score_rate", title: "得分率" },
];

export default function StudentDashboardPanel() {
  /** 筛选条件 */
  const [grade, setGrade] = useState<string>("");
  const [subject, setSubject] = useState<string>("");
  const [semester, setSemester] = useState<string>("");

  /** 获取看板数据 */
  const { data, isLoading } = useQuery<DashboardResponse>({
    queryKey: ["analytics", "student-dashboard", grade, subject, semester],
    queryFn: () =>
      analyticsService.getStudentDashboard({
        ...(grade && { grade }),
        ...(subject && { subject }),
        ...(semester && { semester }),
      }),
  });

  const items = data?.items || [];

  /**
   * 折线图数据：scoreRate 已转换为 0-100 的百分比数值。
   * 预计算 scoreRateLabel 字段，避免依赖 G2 v5 formatter 回调参数格式。
   */
  const chartData = useMemo(
    () =>
      items.map((item) => {
        const rate = +(item.score_rate * 100).toFixed(1);
        return {
          name: item.name,
          xLabel: item.name,          // X 轴标签：作业名称
          scoreRate: rate,            // Y 轴数值（0-100）
          scoreRateLabel: `${rate}%`, // 预计算标签文本（避免 formatter）
          created_at: item.created_at,
        };
      }),
    [items],
  );

  /** G2 v5 折线图配置（@ant-design/charts v2.6） */
  const lineConfig = useMemo(() => {
    if (chartData.length === 0) return null;

    return {
      data: chartData,
      xField: "xLabel",       // X 轴 → encode.x
      yField: "scoreRate",    // Y 轴 → encode.y
      shape: "smooth",        // 平滑曲线 → style.shape（替代旧版 smooth: true）

      /** 数据点样式：圆形点（point 作为子 mark，默认 shape=circle） */
      point: {
        style: { r: 5 },       // 点半径 5px
      },

      /**
       * 数据标签：直接读取预计算的 scoreRateLabel 字段。
       * 放在点下方，避免数字与数据点重叠遮挡。
       * G2 v5 用 dy（像素偏移）+ textBaseline 控制位置，不支持 position/offsetY。
       */
      label: {
        text: "scoreRateLabel", // 直接使用预计算标签文本
        dy: 14,                 // 点半径5px + 9px间距 = 标签向下偏移14px
        textBaseline: "top" as const,  // 文字从锚点向下延伸，配合 dy 保证在点下方
        textAlign: "center" as const,
        style: {
          fill: "#333",
          fontSize: 11,
        },
      },

      /**
       * Y 轴配置（G2 v5 使用 axis.y，替代旧版 yAxis）。
       * scale.y.domain 锁定 0-100 范围。
       */
      scale: {
        y: { domain: [0, 100] },
      },
      axis: {
        y: {
          title: "得分率 (%)",
          labelFormatter: (_v: string) => `${_v}%`,
        },
        x: {
          title: "作业名称",
          labelAutoRotate: true,
          labelAutoHide: true,
          labelAutoEllipsis: true,
        },
      },

      /**
       * 提示框：悬停时显示作业名（name）和得分率（scoreRate）。
       * G2 v5 使用 items 函数数组替代旧版 formatter。
       */
      tooltip: {
        items: [
          (datum: { name: string; scoreRate: number }) => ({
            name: datum.name,                // 显示作业名称
            value: `${datum.scoreRate}%`,     // 显示得分率百分比
          }),
        ],
      },

      height: 350,
      autoFit: true,
    };
  }, [chartData]);

  /** 表格列定义 */
  const tableColumns = [
    {
      title: "作业名称",
      dataIndex: "name",
      key: "name",
      width: 160,
    },
    {
      title: "上传时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (val: string) => formatDate(val, true),
    },
    {
      title: "年级",
      dataIndex: "grade",
      key: "grade",
      width: 80,
    },
    {
      title: "学期",
      dataIndex: "semester",
      key: "semester",
      width: 80,
    },
    {
      title: "科目",
      dataIndex: "subject",
      key: "subject",
      width: 80,
    },
    {
      title: "得分率",
      dataIndex: "score_rate",
      key: "score_rate",
      width: 100,
      render: (val: number) => (
        <Typography.Text
          type={val >= 0.8 ? "success" : val >= 0.6 ? "warning" : "danger"}
          strong
        >
          {(val * 100).toFixed(1)}%
        </Typography.Text>
      ),
    },
  ];

  /** 表格数据 */
  const tableData = items.map((item) => ({
    key: item.id,
    ...item,
  }));

  /** 导出 Excel（异步生成文件） */
  const handleExport = async () => {
    if (items.length === 0) {
      message.warning("暂无数据可导出");
      return;
    }
    // 得分率转为百分比显示
    const exportData = items.map((item) => ({
      ...item,
      created_at: formatDate(item.created_at, true),
      score_rate: `${(item.score_rate * 100).toFixed(1)}%`,
    }));
    await exportToExcel(EXPORT_COLUMNS, exportData as unknown as Record<string, unknown>[], "作业情况统计");
    message.success("导出成功");
  };

  return (
    <div>
      {/* ===== 筛选器 ===== */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space size="middle" wrap>
          <Typography.Text strong>数据筛选：</Typography.Text>
          <Select
            placeholder="年级"
            allowClear
            style={{ width: 130 }}
            value={grade || undefined}
            onChange={(v) => setGrade(v ?? "")}
            options={toSelectOptions(GRADE_OPTIONS)}
          />
          <Select
            placeholder="科目"
            allowClear
            style={{ width: 130 }}
            value={subject || undefined}
            onChange={(v) => setSubject(v ?? "")}
            options={toSelectOptions(SUBJECT_OPTIONS)}
          />
          <Select
            placeholder="学期"
            allowClear
            style={{ width: 130 }}
            value={semester || undefined}
            onChange={(v) => setSemester(v ?? "")}
            options={toSelectOptions(SEMESTER_OPTIONS)}
          />
        </Space>
      </Card>

      {/* ===== 内容区 ===== */}
      {isLoading ? (
        <div style={{ textAlign: "center", padding: 60 }}>
          <Spin size="large" />
        </div>
      ) : items.length > 0 && lineConfig ? (
        <>
          {/* 得分率变化曲线 */}
          <Card title="得分率变化曲线" size="small" style={{ marginBottom: 16 }}>
            <Line {...lineConfig} />
          </Card>

          {/* 作业情况表格 */}
          <Card
            title="作业情况"
            size="small"
            extra={
              <Button
                type="primary"
                icon={<DownloadOutlined />}
                size="small"
                onClick={handleExport}
              >
                导出Excel
              </Button>
            }
          >
            <Table
              columns={tableColumns}
              dataSource={tableData}
              pagination={{ pageSize: 20, showSizeChanger: true }}
              size="middle"
              bordered
              scroll={{ x: 700 }}
            />
          </Card>
        </>
      ) : (
        <Card>
          <Empty description="暂无看板数据，请先完成作业分析" />
        </Card>
      )}
    </div>
  );
}
