/**
 * 学情分析主页面。
 *
 * 通过 Ant Design Tabs 管理三个子板块：
 * 1. 作业统计      — 按科目统计作业数量 + 饼状图
 * 2. 学生学期看板  — 得分率变化曲线 + 作业情况表格（支持 Excel 导出）
 * 3. 知识点热力图  — 知识点考察频次 + 得分率热力图（支持 Excel 导出）
 *
 * Tab 状态同步到 URL query 参数 `?tab=xxx`，支持浏览器前进/后退和书签。
 */

import { useState, useEffect } from "react";
import { Card, Tabs } from "antd";
import {
  BarChartOutlined,
  DashboardOutlined,
  HeatMapOutlined,
} from "@ant-design/icons";
import { useSearchParams } from "react-router-dom";
import HomeworkStatsPanel from "./HomeworkStatsPanel";
import StudentDashboardPanel from "./StudentDashboardPanel";
import KnowledgeHeatmapPanel from "./KnowledgeHeatmapPanel";

/** Tab 配置 */
const TABS = [
  {
    key: "homework-stats",
    label: "作业统计",
    icon: <BarChartOutlined />,
    children: <HomeworkStatsPanel />,
  },
  {
    key: "student-dashboard",
    label: "学生学情看板",
    icon: <DashboardOutlined />,
    children: <StudentDashboardPanel />,
  },
  {
    key: "knowledge-heatmap",
    label: "知识点热力图",
    icon: <HeatMapOutlined />,
    children: <KnowledgeHeatmapPanel />,
  },
];

const DEFAULT_TAB = "homework-stats";

export default function LearningAnalytics() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");

  /** 初始化 activeTab：URL 有合法值则用，否则用默认值 */
  const getInitialTab = (): string => {
    if (tabParam && TABS.some((t) => t.key === tabParam)) {
      return tabParam;
    }
    return DEFAULT_TAB;
  };

  const [activeTab, setActiveTab] = useState(getInitialTab);

  /** 同步 URL 参数（如果 URL 初始就无参数则补上默认值） */
  useEffect(() => {
    if (!tabParam || !TABS.some((t) => t.key === tabParam)) {
      setSearchParams({ tab: DEFAULT_TAB }, { replace: true });
    }
  }, []);

  /** Tab 切换时更新 URL */
  const handleTabChange = (key: string) => {
    setActiveTab(key);
    setSearchParams({ tab: key }, { replace: true });
  };

  return (
    <div>
      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={handleTabChange}
          items={TABS.map((tab) => ({
            key: tab.key,
            label: (
              <span>
                {tab.icon}
                <span style={{ marginLeft: 6 }}>{tab.label}</span>
              </span>
            ),
            children: tab.children,
          }))}
          // 保留已切换过的 Tab 的 DOM 状态，避免来回切换时丢失筛选条件
          destroyInactiveTabPane={false}
        />
      </Card>
    </div>
  );
}
