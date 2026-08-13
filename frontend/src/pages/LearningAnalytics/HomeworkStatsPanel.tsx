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

/**
 * 饼状图配色：低饱和度柔和商务配色（背景白色、标签黑色）
 * - 9 科目固定低饱和色，色相均匀分布（相邻约 40°）、同一亮度/饱和度带，整体协调
 * - 无大红大绿，以蓝-橙为主对比，经 CVD 色盲模拟校验；CVD 下橙/绿等无解对
 *   （色盲视锥限制）由外置标签 + 图例 + 左侧表格兜底
 * - 占比最大的扇区使用同色相"饱和强化版"，其余扇区低饱和弱化，突出重点
 */
const SUBJECT_COLORS: Record<string, string> = {
  语文: "#3A5E96", // 蓝
  数学: "#7A66B0", // 藕紫
  英语: "#B09A48", // 金
  物理: "#4E96A0", // 青
  化学: "#B07084", // 玫瑰
  生物: "#4E7A56", // 深绿
  政治: "#C0674A", // 红橙
  历史: "#8A5E3E", // 棕
  地理: "#5E7A9E", // 蓝灰
};

/** 各科目的"饱和强化版"（同色相、饱和度稍高、亮度略降），用于占比最大的扇区 */
const SATURATED_SUBJECT_COLORS: Record<string, string> = {
  语文: "#2A4D85",
  数学: "#634F99",
  英语: "#9C8435",
  物理: "#3D828C",
  化学: "#9D5D72",
  生物: "#3E6545",
  政治: "#AB5138",
  历史: "#754C30",
  地理: "#4C6486",
};

/** 【其他】扇区颜色：小占比项合并后的中性灰 */
const OTHER_COLOR = "#9E9E9E";

/** 饼图最多显示 TOP 6 科目，其余小占比项合并为【其他】 */
const PIE_MAX_SUBJECTS = 6;

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

export default function HomeworkStatsPanel({ active }: { active?: boolean }) {
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
    // 面板常驻挂载（隐藏而非卸载），tab 未激活时不发请求，激活时才加载
    enabled: active,
  });

  /**
   * 饼状图数据：预计算显示标签，避免依赖 G2 v5 formatter 回调的参数格式。
   * G2 v5 的 label.text 直接读取数据字段，无需 formatter 即可正确显示。
   *
   * 合并逻辑：后端 subject_stats 已按作业数量降序返回，
   * 科目数超过 PIE_MAX_SUBJECTS 时，取前 TOP 6，其余小占比项合并为【其他】。
   */
  const pieData = useMemo(() => {
    const raw = (data?.subject_stats || []).map((item) => ({
      type: item.subject,
      value: item.count,
    }));
    // 降序前 TOP N 名 + 其余合并为【其他】（合并后总数量不变）
    let items = raw;
    if (raw.length > PIE_MAX_SUBJECTS) {
      const restCount = raw
        .slice(PIE_MAX_SUBJECTS)
        .reduce((s, d) => s + d.value, 0);
      items = [...raw.slice(0, PIE_MAX_SUBJECTS), { type: "其他", value: restCount }];
    }
    const total = items.reduce((s, d) => s + d.value, 0);
    // 预计算每个扇区的百分比和完整标签文本
    return items.map((item) => {
      const pct = total > 0 ? ((item.value / total) * 100).toFixed(1) : "0.0";
      return {
        ...item,
        percentage: pct,
        /** 标签文本：科目名换行 + 百分比（G2 v5 直接读取此字段，无需 formatter） */
        pieLabel: `${item.type}\n${pct}%`,
      };
    });
  }, [data]);

  /** G2 v5 饼状图配置（@ant-design/charts v2.6）。
   *  无数据时返回 undefined，渲染层通过 hasPieData 控制显隐。 */
  const pieConfig = useMemo(() => {
    if (pieData.length === 0) return undefined;

    // 占比最大的扇区使用"饱和强化版"突出，其余扇区保持低饱和弱化
    const maxType = pieData.reduce((a, b) => (b.value > a.value ? b : a), pieData[0]).type;
    const colorDomain = pieData.map((d) => d.type);
    const colorRange = pieData.map((d) =>
      d.type === maxType
        ? SATURATED_SUBJECT_COLORS[d.type] || OTHER_COLOR
        : SUBJECT_COLORS[d.type] || OTHER_COLOR
    );

    return {
      data: pieData,
      angleField: "value",   // 扇形角度 → encode.y
      colorField: "type",    // 颜色分类 → encode.color
      innerRadius: 0.5,      // 环形图内半径 → coordinate.innerRadius
      radius: 0.8,           // 外半径 → coordinate.outerRadius

      /** 自定义配色：最大扇区饱和强化，其余按科目低饱和固定色 */
      scale: {
        color: {
          domain: colorDomain,
          range: colorRange,
        },
      },

      /**
       * 标签：直接读取预计算的 pieLabel 字段（含科目名 + 百分比）。
       * 不用 formatter，避免 G2 v5 回调参数格式不一致导致 undefinedNaN%。
       * 文字黑色（#000），白色背景上清晰可读。
       */
      label: {
        text: "pieLabel",
        position: "outside",
        style: {
          fontSize: 11,
          fill: "#000",
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

  /** 是否有饼图数据（显式布尔值，避免 null/undefined 混淆） */
  const hasPieData = pieData.length > 0;

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
              {hasPieData && pieConfig ? (
                <div style={{ position: "relative" }}>
                  <Pie {...pieConfig} />
                  {/* 环形图中心统计文字：G2 v5 不再内置 statistic，用 CSS 绝对定位覆盖 */}
                  <div
                    style={{
                      position: "absolute",
                      top: "50%",
                      left: "45%",
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
