/**
 * 数据看板页面
 *
 * 入口：右上角用户账号下拉菜单 → 数据看板
 *
 * 内容：
 * - 汇总指标卡：日均 Token 消耗量、日均调用次数、累计 Token、累计调用次数
 * - 趋势图表：日均 Token 消耗量统计表（按日聚合，最近日期在前）
 * - 用量统计：日均 Token 消耗量柱状图、日均 Token 消耗量趋势折线图、日调用量趋势折线图
 *
 * 图表库：@ant-design/charts v2.6（底层 G2 v5），API 与 v1.x G2Plot 不同：
 * - smooth: true → shape: "smooth"（G2 v5 样式通道）
 * - yAxis/xAxis → axis: { y: {...}, x: {...} }（G2 v5 轴配置）
 * - tooltip.formatter → tooltip.items 函数数组
 */

import { useMemo, useState } from "react";
import {
  Card, Col, Empty, Row, Select, Space, Spin, Statistic, Table, Typography,
} from "antd";
import {
  ApiOutlined, BarChartOutlined, DashboardOutlined, FireOutlined, ThunderboltOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Column, Line } from "@ant-design/charts";
import { usageService, type TokenUsageResponse, type DailyUsageItem } from "../../services/usageService";

/** 统计区间选项 */
const DAYS_OPTIONS = [
  { value: 7, label: "最近 7 天" },
  { value: 14, label: "最近 14 天" },
  { value: 30, label: "最近 30 天" },
  { value: 90, label: "最近 90 天" },
];

export default function DataDashboard() {
  const [days, setDays] = useState(30);

  const { data, isLoading, isError, error } = useQuery<TokenUsageResponse>({
    queryKey: ["dashboard", "token-usage", days],
    queryFn: () => usageService.getTokenUsage(days),
  });

  // 请求失败时的提示文案（A4-4：原实现没有错误分支，
  // 非管理员访问被 403 后页面渲染成"暂无用量数据"空态，误导用户）
  const errorDescription = useMemo(() => {
    const status = (error as { response?: { status?: number } } | undefined)?.response?.status;
    return status === 403
      ? "数据看板仅管理员可访问，如需开通请联系管理员"
      : "数据加载失败，请稍后重试";
  }, [error]);

  const daily = useMemo(() => data?.daily || [], [data]);
  const summary = data?.summary;
  const hasData = daily.some((d) => d.calls > 0);

  /** 图表数据：日期缩短为 MM-DD 便于 X 轴展示 */
  const chartData = useMemo(
    () =>
      daily.map((item) => ({
        ...item,
        dateLabel: item.date.slice(5), // YYYY-MM-DD → MM-DD
      })),
    [daily],
  );

  /** 共用 X 轴配置（日期轴，自动旋转/抽稀） */
  const xAxis = {
    title: "日期",
    labelAutoRotate: true,
    labelAutoHide: true,
  };

  /** 日均 Token 消耗量柱状图（G2 v5） */
  const tokenColumnConfig = useMemo(
    () => ({
      data: chartData,
      xField: "dateLabel",
      yField: "total_tokens",
      axis: { x: xAxis, y: { title: "Token 数" } },
      style: { fill: "#1677ff", radiusTopLeft: 4, radiusTopRight: 4 },
      tooltip: {
        items: [
          (datum: DailyUsageItem & { dateLabel: string }) => ({
            name: "Token 消耗",
            value: datum.total_tokens.toLocaleString(),
          }),
        ],
      },
      height: 300,
      autoFit: true,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chartData],
  );

  /** 日均 Token 消耗量趋势折线图（G2 v5） */
  const tokenLineConfig = useMemo(
    () => ({
      data: chartData,
      xField: "dateLabel",
      yField: "total_tokens",
      shape: "smooth",
      point: { style: { r: 3 } },
      axis: { x: xAxis, y: { title: "Token 数" } },
      style: { stroke: "#722ed1" },
      tooltip: {
        items: [
          (datum: DailyUsageItem & { dateLabel: string }) => ({
            name: "Token 消耗",
            value: datum.total_tokens.toLocaleString(),
          }),
        ],
      },
      height: 300,
      autoFit: true,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chartData],
  );

  /** 日调用量趋势折线图（G2 v5） */
  const callsLineConfig = useMemo(
    () => ({
      data: chartData,
      xField: "dateLabel",
      yField: "calls",
      shape: "smooth",
      point: { style: { r: 3 } },
      axis: { x: xAxis, y: { title: "调用次数" } },
      style: { stroke: "#52c41a" },
      tooltip: {
        items: [
          (datum: DailyUsageItem & { dateLabel: string }) => ({
            name: "调用次数",
            value: `${datum.calls} 次`,
          }),
        ],
      },
      height: 300,
      autoFit: true,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chartData],
  );

  /** 统计表列定义 */
  const tableColumns = [
    { title: "日期", dataIndex: "date", key: "date", width: 120 },
    {
      title: "调用次数",
      dataIndex: "calls",
      key: "calls",
      width: 100,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: "输入 Token",
      dataIndex: "prompt_tokens",
      key: "prompt_tokens",
      width: 120,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: "输出 Token",
      dataIndex: "completion_tokens",
      key: "completion_tokens",
      width: 120,
      render: (v: number) => v.toLocaleString(),
    },
    {
      title: "总 Token",
      dataIndex: "total_tokens",
      key: "total_tokens",
      width: 120,
      render: (v: number) => (
        <Typography.Text strong>{v.toLocaleString()}</Typography.Text>
      ),
    },
    {
      title: "平均每次消耗",
      key: "avg_per_call",
      width: 120,
      render: (_: unknown, record: DailyUsageItem) =>
        record.calls > 0 ? Math.round(record.total_tokens / record.calls).toLocaleString() : "-",
    },
  ];

  /** 统计表数据：最近日期在前 */
  const tableData = useMemo(
    () => [...daily].reverse().map((item) => ({ key: item.date, ...item })),
    [daily],
  );

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
      {/* ===== 标题与区间筛选 ===== */}
      <Space
        style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}
      >
        <Typography.Title level={3} style={{ margin: 0 }}>
          <DashboardOutlined /> 数据看板
        </Typography.Title>
        <Select
          value={days}
          onChange={setDays}
          options={DAYS_OPTIONS}
          style={{ width: 140 }}
        />
      </Space>

      {isLoading ? (
        <div style={{ textAlign: "center", padding: 60 }}>
          <Spin size="large" />
        </div>
      ) : isError ? (
        // 错误分支：403（非管理员）与网络/服务错误分开提示，不再静默显示"暂无数据"
        <Card>
          <Empty description={errorDescription} />
        </Card>
      ) : (
        <>
          {/* ===== 汇总指标卡 ===== */}
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="日均 Token 消耗量"
                  value={summary?.avg_daily_tokens ?? 0}
                  precision={1}
                  prefix={<FireOutlined style={{ color: "#fa541c" }} />}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="日均调用次数"
                  value={summary?.avg_daily_calls ?? 0}
                  precision={1}
                  prefix={<ThunderboltOutlined style={{ color: "#faad14" }} />}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title={`累计 Token（${days}天）`}
                  value={summary?.total_tokens ?? 0}
                  prefix={<BarChartOutlined style={{ color: "#1677ff" }} />}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title={`累计调用次数（${days}天）`}
                  value={summary?.total_calls ?? 0}
                  prefix={<ApiOutlined style={{ color: "#52c41a" }} />}
                />
              </Card>
            </Col>
          </Row>

          {!hasData ? (
            <Card>
              <Empty description="暂无用量数据，使用 AI 功能后将自动记录 Token 消耗" />
            </Card>
          ) : (
            <>
              {/* ===== 趋势图表：日均 Token 消耗量统计表 ===== */}
              <Card
                title="趋势图表 · 日均 Token 消耗量统计表"
                size="small"
                style={{ marginBottom: 16 }}
              >
                <Table
                  columns={tableColumns}
                  dataSource={tableData}
                  pagination={{ pageSize: 10, showSizeChanger: true }}
                  size="middle"
                  bordered
                  scroll={{ x: 700 }}
                />
              </Card>

              {/* ===== 用量统计：柱状图 + 两条趋势折线图 ===== */}
              <Card
                title="用量统计 · 日均 Token 消耗量柱状图"
                size="small"
                style={{ marginBottom: 16 }}
              >
                <Column {...tokenColumnConfig} />
              </Card>
              <Row gutter={16}>
                <Col xs={24} lg={12}>
                  <Card
                    title="用量统计 · 日均 Token 消耗量趋势折线图"
                    size="small"
                    style={{ marginBottom: 16 }}
                  >
                    <Line {...tokenLineConfig} />
                  </Card>
                </Col>
                <Col xs={24} lg={12}>
                  <Card
                    title="用量统计 · 日调用量趋势折线图"
                    size="small"
                    style={{ marginBottom: 16 }}
                  >
                    <Line {...callsLineConfig} />
                  </Card>
                </Col>
              </Row>
            </>
          )}
        </>
      )}
    </div>
  );
}
