import { Layout, Button, Space, Typography, Dropdown } from "antd";
import { LogoutOutlined, UserOutlined, SettingOutlined, SmileOutlined, DashboardOutlined, TeamOutlined } from "@ant-design/icons";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";

const { Header: AntHeader } = Layout;

/** 顶部导航菜单项：key 与路由前缀一一对应，统一由数组驱动，避免重复 JSX */
const NAV_ITEMS = [
  { key: "assignments", label: "作业管理", path: "/assignments" },
  { key: "analytics", label: "学情分析", path: "/analytics" },
  { key: "oral", label: "听力与口语", path: "/oral" },
  { key: "composition", label: "作文批改", path: "/composition" },
];

export default function Header() {
  const { logout, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  // 当前激活页签：按路径前缀匹配（/assignments/:id 等详情页同样命中），兜底作业管理
  const currentTab =
    NAV_ITEMS.find((item) => location.pathname.startsWith(item.path))?.key ??
    "assignments";

  /** 用户下拉菜单项 */
  const userMenuItems = [
    {
      key: "personality",
      icon: <SmileOutlined />,
      label: "助教设置",
      onClick: () => navigate("/settings/personality"),
    },
    {
      key: "dashboard",
      icon: <DashboardOutlined />,
      label: "数据看板",
      onClick: () => navigate("/dashboard"),
    },
    // 仅超级管理员（role=admin）可见"账号设置"入口（新增账号的唯一途径）
    ...(user?.role === "admin"
      ? [
          {
            key: "account",
            icon: <TeamOutlined />,
            label: "账号设置",
            onClick: () => navigate("/settings/account"),
          },
        ]
      : []),
    { type: "divider" as const },
    {
      key: "logout",
      icon: <LogoutOutlined />,
      label: "退出登录",
      onClick: logout,
    },
  ];

  return (
    <AntHeader
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        // 调高导航栏，容纳更大的页签按钮（见下方按钮 padding/fontSize）
        height: 76,
        lineHeight: "76px",
        // 浅色基底：半透明白 + blur 毛玻璃效果由 soft-ui.css 统一提供，
        // 此处只定实色兜底（被 CSS 覆盖时也无碍）
        background: "rgba(255, 255, 255, 0.8)",
        padding: "0 24px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 32 }}>
        <Typography.Title
          level={3}
          style={{ color: "var(--soft-text-primary)", margin: 0, fontSize: 26, lineHeight: "34px" }}
        >
          AI 助教
        </Typography.Title>
        <Space size={8}>
          {NAV_ITEMS.map((item) => {
            const active = currentTab === item.key;
            return (
              <Button
                key={item.key}
                type="text"
                onClick={() => navigate(item.path)}
                style={{
                  // 激活页签：主色浅蓝底 + 深蓝加粗文字（与侧边栏菜单选中态
                  // 一致，浅色玻璃导航上的克制高亮）；非激活为次级文字色
                  color: active
                    ? "var(--soft-primary)"
                    : "var(--soft-text-secondary)",
                  fontWeight: active ? 700 : 500,
                  background: active
                    ? "rgba(26, 86, 219, 0.08)"
                    : "transparent",
                  borderRadius: 10,
                  // 大号页签：字号/高度明显大于作文批改的 52px 分段切换器
                  fontSize: 18,
                  lineHeight: "24px",
                  minHeight: 56,
                  padding: "0 20px",
                }}
              >
                {item.label}
              </Button>
            );
          })}
        </Space>
      </div>
      <Dropdown
        menu={{ items: userMenuItems }}
        placement="bottomRight"
        overlayClassName="header-user-dropdown"
      >
        <div style={{ cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 6 }}>
          <Typography.Text style={{ color: "var(--soft-text-primary)" }}>
            <UserOutlined /> {user?.username || user?.phone}
          </Typography.Text>
          <SettingOutlined style={{ color: "var(--soft-text-secondary)", fontSize: 12 }} />
        </div>
      </Dropdown>
    </AntHeader>
  );
}
