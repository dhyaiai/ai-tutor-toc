import { useState } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import { Layout, Menu } from "antd";
import {
  UploadOutlined,
  FileTextOutlined,
  ExclamationCircleOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";

const { Sider, Content } = Layout;

const menuItems = [
  { key: "/assignments/upload", icon: <UploadOutlined />, label: "上传作业" },
  { key: "/assignments/records", icon: <FileTextOutlined />, label: "作业记录" },
  { key: "/assignments/error-redo", icon: <ExclamationCircleOutlined />, label: "错题重做" },
  { key: "/assignments/ai-challenge", icon: <ThunderboltOutlined />, label: "繁星驱动" },
];

export default function AssignmentLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <Layout style={{ background: "#f5f5f5" }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        style={{
          background: "#fff",
          position: "sticky",
          top: 0,
          height: "100vh",
          overflow: "auto",
          alignSelf: "flex-start",
        }}
      >
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ paddingTop: 16 }}
        />
      </Sider>
      <Content style={{ padding: "0 24px", minHeight: 360 }}>
        <Outlet />
      </Content>
    </Layout>
  );
}
