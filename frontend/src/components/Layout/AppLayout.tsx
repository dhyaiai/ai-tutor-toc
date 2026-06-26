import { Outlet } from "react-router-dom";
import { Layout } from "antd";
import Header from "./Header";

const { Content } = Layout;

export default function AppLayout() {
  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Header />
      <Content style={{ padding: 24, background: "#f5f5f5" }}>
        <Outlet />
      </Content>
    </Layout>
  );
}
