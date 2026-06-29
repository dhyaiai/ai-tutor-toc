/**
 * 子板块1：作业统计
 *
 * 功能：
 * - 按年级、学期筛选已完成作业
 * - 左侧表格：各科目作业数量 + 总计数行
 * - 右侧饼状图：各科目作业数量占比
 * - 筛选条件变化时表格和图表联动更新
 *
 * 图表库：@ant-design/charts v2.6（底层 G2 v5），API 与 v1.x G2Plot 不同：
 * - color 回调 → scale.color.range / domain
 * - label.content 模板 → label.text + label.formatter
 * - tooltip.formatter → tooltip.items 函数数组
 * - statistic 不再内置 → 用 CSS 绝对定位覆盖环形图中心
 * - interactions → 移除（G2 v5 默认已含基础交互）
 */

import { useState, useMemo } from "react";
import { Card, Select, Space, Typography, Spin, Row, Col, Table, Empty, Tag } from "antd";
import { useQuery } from "@tanstack/react-query";
import { Pie } from "@ant-design/charts";
import { analyticsService, type HomeworkStatsResponse } from "../../services/analyticsService";
import {
  GRADE_OPTIONS,
  SEMESTER_OPTIONS,
  toSelectOptions,
} from "../../utils/filterConfig";

/** 饼状图配色方案（按科目固定颜色，区分度大） */
const SUBJECT_COLORS: Record<string, string> = {
  语文: "#f56a00",
  数学: "#7265e6",
  英语: "#ffbf00",
  物理: "#00a2ae",
  化学: "#0e9e56",
  生物: "#e8590c",
  政治: "#c41a1a",
  历史: "#6e71c4",
  地理: "#1d953f",
};

/** 表格列定义 */
const COLUMNS = [
  {
    title: "科目",
    dataIndex: "subject",
    key: "subject",
    render: (subject: string) => (
      <Tag color={SUBJECT_COLORS[subject] || "default"}>{subject}</Tag>
    ),
  },
  {
    title: "作业数量",
    dataIndex: "count",
    key: "count",
    align: "right" as const,
  },
];

export default function HomeworkStatsPanel() {
  /** 筛选条件 */
  const [grade, setGrade] = useState<string>("");
  const [semester, setSemester] = useState<string>("");

  /** 获取作业统计数据 */
  const { data, isLoading } = useQuery<HomeworkStatsResponse>({
    queryKey: ["analytics", "homework-stats", grade, semester],
    queryFn: () =>
      analyticsService.getHomeworkStats({
        ...(grade && { grade }),
        ...(semester && { semester }),
      }),
  });

  /**
   * 饼状图数据：预计算显示标签，避免依赖 G2 v5 formatter 回调的参数格式。
   * G2 v5 的 label.text 直接读取数据字段，无需 formatter 即可正确显示。
   */
  const pieData = useMemo(() => {
    const raw = (data?.subject_stats || []).map((item) => ({
      type: item.subject,
      value: item.count,
    }));
    const total = raw.reduce((s, d) => s + d.value, 0);
    // 预计算每个扇区的百分比和完整标签文本
    return raw.map((item) => {
      const pct = total > 0 ? ((item.value / total) * 100).toFixed(1) : "0.0";
      return {
        ...item,
        percentage: pct,
        /** 标签文本：科目名换行 + 百分比（G2 v5 直接读取此字段，无需 formatter） */
        pieLabel: `${item.type}\n${pct}%`,
      };
    });
  }, [data]);

  /** G2 v5 饼状图配置（@ant-design/charts v2.6） */
  const pieConfig = useMemo(() => {
    if (pieData.length === 0) return null;

    // 根据数据动态构建颜色映射，保证每科颜色固定
    const colorDomain = pieData.map((d) => d.type);
    const colorRange = pieData.map((d) => SUBJECT_COLORS[d.type] || "#888888");

    return {
      data: pieData,
      angleField: "value",   // 扇形角度 → encode.y
      colorField: "type",    // 颜色分类 → encode.color
      innerRadius: 0.5,      // 环形图内半径 → coordinate.innerRadius
      radius: 0.8,           // 外半径 → coordinate.outerRadius

      /** 自定义配色：按科目映射固定颜色 */
      scale: {
        color: {
          domain: colorDomain,
          range: colorRange,
        },
      },

      /**
       * 标签：直接读取预计算的 pieLabel 字段（含科目名 + 百分比）。
       * 不用 formatter，避免 G2 v5 回调参数格式不一致导致 undefinedNaN%。
       */
      label: {
        text: "pieLabel",
        position: "outside",
        style: {
          fontSize: 11,
          fill: "#333",
        },
      },

      /** 提示框：悬停显示"科目 N 次"（G2 v5 用 items 函数数组替代 formatter） */
      tooltip: {
        items: [
          (datum: { type: string; value: number }) => ({
            name: datum.type,
            value: `${datum.value} 次`,
          }),
        ],
      },

      /** 图例：右上角竖向排列（G2 v5 用 color 通道 + layout: vertical） */
      legend: {
        color: { position: "right", layout: "vertical" },
      },

      height: 350,
      autoFit: true,
    };
  }, [pieData]);

  /** 表格数据 */
  const tableData = (data?.subject_stats || []).map((item, index) => ({
    key: index,
    subject: item.subject,
    count: item.count,
  }));

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
            placeholder="学期"
            allowClear
            style={{ width: 130 }}
            value={semester || undefined}
            onChange={(v) => setSemester(v ?? "")}
            options={toSelectOptions(SEMESTER_OPTIONS)}
          />
        </Space>
      </Card>

      {/* ===== 表格 + 饼状图 ===== */}
      {isLoading ? (
        <div style={{ textAlign: "center", padding: 60 }}>
          <Spin size="large" />
        </div>
      ) : data && data.subject_stats.length > 0 ? (
        <Row gutter={24}>
          {/* 左侧：科目作业数量表格 */}
          <Col xs={24} lg={12}>
            <Card title="各科目作业数量" size="small">
              <Table
                columns={COLUMNS}
                dataSource={tableData}
                pagination={false}
                size="middle"
                bordered
                summary={() => (
                  <Table.Summary.Row>
                    <Table.Summary.Cell index={0}>
                      <Typography.Text strong>合计</Typography.Text>
                    </Table.Summary.Cell>
                    <Table.Summary.Cell index={1} align="right">
                      <Typography.Text strong>{data.total}</Typography.Text>
                    </Table.Summary.Cell>
                  </Table.Summary.Row>
                )}
              />
            </Card>
          </Col>

          {/* 右侧：饼状图（环形图 + CSS 居中统计文字） */}
          <Col xs={24} lg={12}>
            <Card title="各科目作业占比" size="small">
              {pieConfig ? (
                <div style={{ position: "relative" }}>
                  <Pie {...pieConfig} />
                  {/* 环形图中心统计文字：G2 v5 不再内置 statistic，用 CSS 绝对定位覆盖 */}
                  <div
                    style={{
                      position: "absolute",
                      top: "50%",
                      left: "50%",
                      transform: "translate(-50%, -55%)",
                      textAlign: "center",
                      pointerEvents: "none",
                      userSelect: "none",
                    }}
                  >
                    <div style={{ fontSize: 22, color: "#8c8c8c", lineHeight: 1.4, fontWeight: 500 }}>
                      合计
                    </div>
                    <div style={{ fontSize: 24, fontWeight: 700, color: "#262626", lineHeight: 1.3 }}>
                      {data.total}份
                    </div>
                  </div>
                </div>
              ) : (
                <Empty description="暂无数据" />
              )}
            </Card>
          </Col>
        </Row>
      ) : (
        <Card>
          <Empty description="暂无作业统计数据，请先完成作业分析" />
        </Card>
      )}
    </div>
  );
}
