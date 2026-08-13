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
 *
 * 视觉改造要点（亮色主题，蓝紫→青蓝渐变）：
 * - 线条发光/阴影：style 的 shadowColor/shadowBlur（@antv/g 透传 Canvas 阴影）
 * - 区域渐变填充：area.style.fill 支持 CSS linear-gradient 语法
 * - 入场动画：animation.appear（'path-in' 线条生长），v1 的 animate/enter 已废弃
 * - hover 高亮：point.state.active 配置状态样式 + tooltip.crosshairs 参考线
 * - 毛玻璃 tooltip：tooltip.domStyles 覆盖 .g2-tooltip 容器的 backdropFilter
 * - 弱化坐标轴：axis.{x,y}.grid: null / tick: null / 极淡轴线
 */

import { useState, useMemo, useRef, useEffect } from "react";
import {
  Card, Select, Space, Typography, Spin, Table, Empty, Button, message, Progress, Slider,
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

/* ==================== 图表视觉常量（亮色主题，蓝紫→青蓝渐变） ==================== */

/** 全图表统一无衬线字体栈（数字用 Segoe UI/Roboto，中文回退到苹方/微软雅黑） */
const CHART_FONT =
  `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", ` +
  `"Hiragino Sans GB", "Microsoft YaHei", sans-serif`;

/** 主色：蓝紫（折线、数据点描边、tooltip 主题色），低饱和不刺眼 */
const MAIN_COLOR = "#5b7cfa";

/** 辅色：青蓝（渐变末端、hover 高亮点），与主色构成蓝紫→青蓝渐变 */
const ACCENT_COLOR = "#38bdf8";

/** 折线下方的区域渐变填充：顶部蓝紫半透明 → 底部青蓝近透明（配合白色背景） */
const AREA_GRADIENT =
  `linear-gradient(180deg, rgba(91, 124, 250, 0.26) 0%, ` +
  `rgba(56, 189, 248, 0.10) 50%, rgba(56, 189, 248, 0) 100%)`;

/** 坐标轴弱化颜色：浅灰蓝，让视线集中在数据本身 */
const AXIS_COLOR = "#8c94a6";

/** 坐标轴线颜色：极淡的浅灰，仅保留基准感，不构成视觉干扰 */
const AXIS_LINE_COLOR = "#e9edf6";

/**
 * X 轴范围滑块配置（左右拖动）：
 * - 拖动中间区域 → 左右平移选区（浏览不同时间段的曲线）
 * - 拖动两端手柄 → 缩放选区范围（聚焦某几份作业）
 * 样式与主题统一：蓝紫选区 + 白色圆角手柄 + 极淡轨道。
 *
 * 注意：G2 v5 自带的 slider 组件在 @ant-design/plots 的配置管线中无法
 * 正常渲染（spec 经 transformOptions 转换后组件丢失），因此这里不使用
 * G2 slider，而是用 antd 的 Slider（range 双端滑块）驱动 React 状态，
 * 对图表数据切片显示——功能等同且渲染完全可控。
 */

export default function StudentDashboardPanel({ active }: { active?: boolean }) {
  /** 筛选条件（科目默认选中数学） */
  const [grade, setGrade] = useState<string>("");
  const [subject, setSubject] = useState<string>("数学");
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
    // 面板常驻挂载（隐藏而非卸载），tab 未激活时不发请求，激活时才加载
    enabled: active,
  });

  /** 用 useMemo 稳定 items 引用，避免 data 为 undefined 时每次渲染创建新空数组
   *  导致下方 prevItemsRef 比对引用永远不同，触发 setRange 死循环白屏 */
  const items = useMemo(() => data?.items ?? [], [data]);

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

  /**
   * X 轴范围滑块（左右拖动浏览/聚焦曲线）：
   * 作业数量多时整条曲线挤在一起看不清，用 range 滑块选择显示范围：
   * - 拖动中间区域 → 左右平移选区（浏览不同时间段的曲线）
   * - 拖动两端手柄 → 缩放选区范围（聚焦某几份作业）
   * 图表只渲染选区内的数据（slice），数据量小时滑块自动隐藏。
   *
   * 使用 React 推荐的"渲染期间调整 state"模式（类似 getDerivedStateFromProps）：
   * 使用 useEffect 监听 items 变化时重置 range，避免渲染中 setState 反模式。
    * 虽然可能有一次渲染的延迟，但符合 React 数据流规范，避免 Strict Mode 下的脉冲问题。
    */
  const [range, setRange] = useState<[number, number] | null>(null);
  const prevItemsRef = useRef(items);
  useEffect(() => {
    if (items !== prevItemsRef.current) {
      prevItemsRef.current = items;
      // items 引用变化（数据加载/筛选切换）→ 重置为完整范围
      setRange([0, Math.max(0, items.length - 1)]);
    }
  }, [items]);
  // 按选区切片，只绘制范围内的作业；range 未就绪时显示全部数据（兜底）
  const visibleChartData = useMemo(
    () => {
      if (items.length === 0) return [];
      const [start, end] = range ?? [0, items.length - 1];
      return chartData.slice(start, end + 1);
    },
    [chartData, range, items.length],
  );

  /** G2 v5 折线图配置（@ant-design/charts v2.6） */
  const lineConfig = useMemo(() => {
    if (visibleChartData.length === 0) return null;

    return {
      data: visibleChartData,
      xField: "xLabel",       // X 轴 → encode.x
      yField: "scoreRate",    // Y 轴 → encode.y
      shape: "smooth",        // 平滑曲线 → style.shape（替代旧版 smooth: true）

      /**
       * 折线样式：2.5px 主色线条 + 轻微发光效果。
       * G2 v5 样式通道支持 shadowColor/shadowBlur（@antv/g 渲染透传），
       * 发光值控制得较小，保证数据清晰可读而不是堆砌特效。
       */
      style: {
        stroke: MAIN_COLOR,                                   // 线条主色：蓝紫
        lineWidth: 2.5,                                       // 线宽 2.5px，视觉上醒目但不厚重
        shadowColor: "rgba(91, 124, 250, 0.38)",              // 发光颜色：与主色同源的半透明蓝紫
        shadowBlur: 10,                                       // 发光半径 10px（轻微光晕）
        shadowOffsetY: 3,                                     // 光晕向下偏移 3px，模拟柔光
      },

      /**
       * 折线下方的区域渐变填充（area 作为子 mark）。
       * 使用 CSS linear-gradient 语法（G2 v5 原生支持）：
       * 顶部蓝紫半透明 → 底部青蓝近透明，与白色卡片背景自然融合。
       */
      area: {
        style: {
          fill: AREA_GRADIENT,
          fillOpacity: 1,       // 透明度已内嵌在渐变色中，这里保持完整
        },
      },

      /**
       * 数据点样式：白色圆点 + 蓝紫描边，与线条同色系。
       * state.active：hover 高亮时圆点放大并变为青蓝填充（与渐变末端呼应）。
       * G2 v5 的 mark spec 支持 state 字段配置 active/inactive 状态样式。
       */
      point: {
        style: {
          r: 5,                 // 默认点半径 5px
          fill: "#ffffff",      // 白底，避免点与线粘连糊成一片
          stroke: MAIN_COLOR,   // 蓝紫描边
          lineWidth: 2,
        },
        state: {
          active: {
            style: {
              r: 7,             // hover 时放大到 7px
              fill: ACCENT_COLOR, // 高亮为青蓝
              stroke: MAIN_COLOR,
              lineWidth: 2,
            },
          },
        },
      },

      /**
       * 入场/更新动画：
       * - appear：首次加载时线条自左向右生长（path-in）+ 淡入，时长 1s 平滑过渡
       * - update：切换筛选条件后数据更新，旧线淡出、新线淡入，避免生硬跳变
       * G2 v5 的 animation 字段名与 G2Plot v1 不同（v1 是 animate/enter 等）。
       */
      animation: {
        appear: { animation: "path-in", duration: 1000, easing: "ease-out" },
        update: { animation: "fade-in", duration: 400 },
      },

      /**
       * 数据标签：直接读取预计算的 scoreRateLabel 字段，数字加大加粗（无衬线字体）。
       * 放在点下方，避免数字与数据点重叠遮挡；
       * autoHide 在作业数量多、标签互相挤压时自动隐藏部分，防止文字堆积。
       */
      label: {
        text: "scoreRateLabel", // 直接使用预计算标签文本
        dy: 16,                 // 点半径5px + 11px间距 = 标签向下偏移16px
        textBaseline: "top" as const,  // 文字从锚点向下延伸，配合 dy 保证在点下方
        textAlign: "center" as const,
        autoHide: true,         // 数据点多时自动隐藏重叠标签，保证可读性
        style: {
          fill: "#4a5468",      // 深灰蓝文字，不抢数据点风头
          fontSize: 13,         // 数字加粗放大，一眼看清得分率
          fontWeight: 700,
          fontFamily: CHART_FONT,
        },
      },

      /**
       * 坐标轴弱化：
       * - grid: null 去掉 Y 轴横向网格线，减少干扰
       * - line 用极淡的浅灰线仅保留基准感
       * - tick: null 去掉刻度短线
       * - Y 轴数字加大加粗（fontWeight 700），X 轴作业名保持常规字号
       */
      scale: {
        y: { domain: [0, 100] },  // 锁定 0-100 范围，避免百分比曲线被拉伸失真
        /**
         * x 轴显示范围留出 4% 的左右边距：
         * band scale 默认第一个点紧贴绘图区左边缘（会与纵坐标重叠），
         * range 限制数据只绘制在绘图区的 4%~96%，首尾点都留出呼吸空间。
         */
        x: { range: [0.04, 0.96] },
      },
      axis: {
        y: {
          grid: null,                     // 去掉网格线
          tick: null,                     // 去掉刻度短线
          line: { stroke: AXIS_LINE_COLOR }, // 极淡基线
          labelFormatter: (_v: string) => `${_v}%`,
          labelFill: AXIS_COLOR,
          labelFontSize: 13,              // 数字加大
          labelFontWeight: 700,           // 数字加粗
          labelFontFamily: CHART_FONT,
        },
        x: {
          tick: null,                     // 去掉刻度短线
          line: { stroke: AXIS_LINE_COLOR }, // 极淡基线
          labelAutoRotate: true,
          labelAutoHide: true,
          labelFill: AXIS_COLOR,
          labelFontSize: 12,
          labelFontFamily: CHART_FONT,
          /**
           * 超长作业名会把旋转后的标签区撑高、压缩绘图区，
           * 这里统一截断为前 8 个字符 + 省略号，完整名称通过 tooltip 查看。
           * （labelAutoEllipsis 对旋转标签不生效，故手动 formatter 截断）
           */
          labelFormatter: (name: string) =>
            name.length > 8 ? `${name.slice(0, 8)}…` : name,
        },
      },

      /**
       * 提示框（tooltip）：
       * - items：标题为作业名（x 轴值），系列名显示为"得分率"，避免与标题重复
       * - crosshairs：hover 时显示淡蓝紫色 X 轴参考线，辅助定位当前作业
       * - domStyles：圆角 + 半透明毛玻璃（backdrop-filter blur），现代感但克制
       */
      tooltip: {
        items: [
          (datum: { scoreRate: number }) => ({
            name: "得分率",               // 系列名（标题栏已显示作业名，这里不再重复）
            value: `${datum.scoreRate}%`,  // 显示得分率百分比
          }),
        ],
        crosshairs: {
          stroke: MAIN_COLOR,             // 参考线颜色与主色一致
          strokeOpacity: 0.22,            // 低透明度，不遮挡数据
          lineWidth: 1,
        },
        domStyles: {
          // 毛玻璃容器：半透明白底 + 背景模糊 + 圆角 + 柔和阴影
          "g2-tooltip": {
            borderRadius: "12px",
            background: "rgba(255, 255, 255, 0.78)",
            backdropFilter: "blur(10px)",
            WebkitBackdropFilter: "blur(10px)",   // Safari 兼容前缀
            boxShadow: "0 8px 24px rgba(60, 80, 180, 0.14)",
            border: "1px solid rgba(91, 124, 250, 0.16)",
            padding: "10px 14px",
            fontFamily: CHART_FONT,
          },
          // 标题（作业名）：常规字重，灰色
          "g2-tooltip-title": {
            fontSize: "13px",
            fontWeight: 600,
            color: "#333",
            marginBottom: "4px",
          },
          // 数值：加大加粗 + 主题蓝紫色
          "g2-tooltip-value": {
            fontSize: "16px",
            fontWeight: 700,
            color: MAIN_COLOR,
          },
          // 系列名：弱化灰
          "g2-tooltip-name": {
            fontSize: "12px",
            color: "#8c94a6",
          },
        },
      },

      height: 350,
      autoFit: true,      // 自适应宽度，手机端自动压缩适配
    };
  }, [visibleChartData]);

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
      width: 180,
      /**
       * 进度条 + 百分比文本：
       * 得分率是 0~100 的连续数值，纯数字列在同一宽度下会显得密集堆积。
       * 改为细进度条直观呈现高低，百分比数字保留供精确读取。
       */
      render: (val: number) => {
        const pct = (val * 100).toFixed(1);
        return (
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {/* 细进度条：蓝紫→青蓝渐变，与上方折线图同色系 */}
            <Progress
              percent={Number(pct)}
              size="small"
              strokeColor={{
                "0%": MAIN_COLOR,
                "100%": ACCENT_COLOR,
              }}
              style={{ flex: 1, minWidth: 80, margin: 0 }}
            />
            <Typography.Text strong style={{ minWidth: 46, textAlign: "right" }}>
              {pct}%
            </Typography.Text>
          </div>
        );
      },
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
            {/* 范围滑块：作业超过 1 份才显示，拖动左右手柄缩放、拖中间平移 */}
            {items.length > 1 && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "6px 10px 2px",
                }}
              >
                <Typography.Text
                  type="secondary"
                  style={{ fontSize: 12, whiteSpace: "nowrap" }}
                >
                  显示范围
                </Typography.Text>
                <Slider
                  range
                  min={0}
                  max={items.length - 1}
                  value={range ?? [0, items.length - 1]}
                  onChange={(v) => setRange(v as [number, number])}
                  // 手柄 tooltip 显示第几份作业
                  tooltip={{
                    formatter: (v?: number) =>
                      v === undefined ? "" : `第 ${v + 1} 份`,
                  }}
                  // 与图表主题统一：蓝紫→青蓝渐变轨道 + 白底蓝边圆角手柄
                  styles={{
                    rail: { backgroundColor: AXIS_LINE_COLOR },
                    track: {
                      background: `linear-gradient(90deg, ${MAIN_COLOR}, ${ACCENT_COLOR})`,
                    },
                    handle: {
                      borderColor: MAIN_COLOR,
                      backgroundColor: "#ffffff",
                      boxShadow: `0 0 6px rgba(91, 124, 250, 0.35)`,
                    },
                  }}
                  style={{ flex: 1, minWidth: 0 }}
                />
                <Typography.Text
                  type="secondary"
                  style={{ fontSize: 12, whiteSpace: "nowrap" }}
                >
                  共 {items.length} 份
                </Typography.Text>
              </div>
            )}
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
              /**
               * 每页 10 条：避免一次性渲染过多行导致数据堆积，
               * 配合进度条列让每条记录都清晰可读。
               */
              pagination={{ pageSize: 10, showSizeChanger: true, showTotal: (t) => `共 ${t} 份作业` }}
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
