import { Layout, Button, Space, Typography, Dropdown } from "antd";
import { LogoutOutlined, UserOutlined, SettingOutlined, SmileOutlined } from "@ant-design/icons";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";

const { Header: AntHeader } = Layout;

export default function Header() {
  const { logout, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const currentTab = location.pathname.startsWith("/analytics")
    ? "analytics"
    : location.pathname.startsWith("/oral")
    ? "oral"
    : location.pathname.startsWith("/composition")
    ? "composition"
    : "assignments";

  /** 用户下拉菜单项 */
  const userMenuItems = [
    {
      key: "personality",
      icon: <SmileOutlined />,
      label: "助教设置",
      onClick: () => navigate("/settings/personality"),
    },
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
        background: "#001529",
        padding: "0 24px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 32 }}>
        <Typography.Title level={4} style={{ color: "#fff", margin: 0 }}>
          AI 助教
        </Typography.Title>
        <Space size="large">
          <Button
            type={currentTab === "assignments" ? "primary" : "text"}
            style={currentTab === "assignments" ? {} : { color: "#fff" }}
            onClick={() => navigate("/assignments")}
          >
            作业管理
          </Button>
          <Button
            type={currentTab === "analytics" ? "primary" : "text"}
            style={currentTab === "analytics" ? {} : { color: "#fff" }}
            onClick={() => navigate("/analytics")}
          >
            学情分析
          </Button>
          <Button
            type={currentTab === "oral" ? "primary" : "text"}
            style={currentTab === "oral" ? {} : { color: "#fff" }}
            onClick={() => navigate("/oral")}
          >
            听力与口语
          </Button>
          <Button
            type={currentTab === "composition" ? "primary" : "text"}
            style={currentTab === "composition" ? {} : { color: "#fff" }}
            onClick={() => navigate("/composition")}
          >
            作文批改
          </Button>
        </Space>
      </div>
      <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
        <Space style={{ cursor: "pointer" }}>
          <Typography.Text style={{ color: "#fff" }}>
            <UserOutlined /> {user?.username}
          </Typography.Text>
          <SettingOutlined style={{ color: "#fff", fontSize: 12 }} />
        </Space>
      </Dropdown>
    </AntHeader>
  );
}
