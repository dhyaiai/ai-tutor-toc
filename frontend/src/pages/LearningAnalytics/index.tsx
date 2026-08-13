/**
 * 学情分析主页面。
 *
 * 通过大号分段切换器管理三个子板块（与作文批改的语文/英语切换一致）：
 * 1. 作业统计      — 按科目统计作业数量 + 饼状图
 * 2. 学生学期看板  — 得分率变化曲线 + 作业情况表格（支持 Excel 导出）
 * 3. 知识点热力图  — 知识点考察频次 + 得分率热力图（支持 Excel 导出）
 *
 * 子板块状态同步到 URL query 参数 `?tab=xxx`，支持浏览器前进/后退和书签。
 */

import { useState, useEffect } from "react";
import { Segmented, Typography } from "antd";
import {
  BarChartOutlined,
  DashboardOutlined,
  HeatMapOutlined,
} from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import HomeworkStatsPanel from "./HomeworkStatsPanel";
import StudentDashboardPanel from "./StudentDashboardPanel";
import KnowledgeHeatmapPanel from "./KnowledgeHeatmapPanel";

const { Title } = Typography;

/** 子板块切换键：homework-stats=作业统计 / student-dashboard=学生学情看板 / knowledge-heatmap=知识点热力图 */
type AnalyticsTabKey = "homework-stats" | "student-dashboard" | "knowledge-heatmap";

const DEFAULT_TAB: AnalyticsTabKey = "homework-stats";

/** 校验 URL tab 参数是否合法 */
function isValidTab(v: string | null): v is AnalyticsTabKey {
  return v === "homework-stats" || v === "student-dashboard" || v === "knowledge-heatmap";
}

export default function LearningAnalytics() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const [activeTab, setActiveTab] = useState<AnalyticsTabKey>(DEFAULT_TAB);

  // URL 是子板块的唯一事实来源（D4）：浏览器前进/后退、直接修改地址、
  // 从其他页面带参数导航过来，都会经由此处把 activeTab 同步到 URL 值；
  // URL 无参数或非法时补写默认值（replace 不产生历史记录）
  useEffect(() => {
    if (isValidTab(tabParam)) {
      setActiveTab(tabParam);
    } else {
      setSearchParams({ tab: DEFAULT_TAB }, { replace: true });
    }
  }, [tabParam, setSearchParams]);

  /** 切换子板块：只更新 URL，activeTab 由上面的 effect 从 URL 同步 */
  const handleTabChange = (key: string | number) => {
    setSearchParams({ tab: key as AnalyticsTabKey }, { replace: true });
  };

  return (
    <div style={{ padding: "12px 0 24px", maxWidth: 1280, margin: "0 auto" }}>
      {/* 页面头部：无 emoji，纯排版层次 */}
      <div style={{ marginBottom: 24 }}>
        <Title level={2} style={{ marginBottom: 0, letterSpacing: "-0.02em" }}>
          学情分析
        </Title>
      </div>

      {/* 大号分段切换：激活项深蓝实底白字（样式见 soft-ui.css 8.1 节，与作文批改语文/英语切换一致） */}
      <Segmented
        block
        className="soft-section-switcher"
        value={activeTab}
        onChange={handleTabChange}
        options={[
          { value: "homework-stats", label: "作业统计", icon: <BarChartOutlined /> },
          { value: "student-dashboard", label: "学生学情看板", icon: <DashboardOutlined /> },
          { value: "knowledge-heatmap", label: "知识点热力图", icon: <HeatMapOutlined /> },
        ]}
      />

      {/* 三个子板块常驻挂载（隐藏而非卸载），切换时保留各自的状态；
           active 传给面板控制 useQuery enabled，未激活的 tab 不发出查询请求 */}
      <div hidden={activeTab !== "homework-stats"} style={{ marginTop: 24 }}>
        <HomeworkStatsPanel active={activeTab === "homework-stats"} />
      </div>
      <div hidden={activeTab !== "student-dashboard"} style={{ marginTop: 24 }}>
        <StudentDashboardPanel active={activeTab === "student-dashboard"} />
      </div>
      <div hidden={activeTab !== "knowledge-heatmap"} style={{ marginTop: 24 }}>
        <KnowledgeHeatmapPanel active={activeTab === "knowledge-heatmap"} />
      </div>
    </div>
  );
}
