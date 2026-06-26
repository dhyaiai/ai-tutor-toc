import { Layout, Button, Space, Typography } from "antd";
import { LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";

const { Header: AntHeader } = Layout;

export default function Header() {
  const { logout, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const currentTab = location.pathname.startsWith("/analytics") ? "analytics" : "assignments";

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
        </Space>
      </div>
      <Space>
        <Typography.Text style={{ color: "#fff" }}>
          <UserOutlined /> {user?.username}
        </Typography.Text>
        <Button type="text" icon={<LogoutOutlined />} style={{ color: "#fff" }} onClick={logout}>
          退出
        </Button>
      </Space>
    </AntHeader>
  );
}
