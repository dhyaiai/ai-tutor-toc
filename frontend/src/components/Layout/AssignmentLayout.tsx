import { useState } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import { ConfigProvider, Layout, Menu, Button, Divider } from "antd";
import {
  UploadOutlined,
  FileTextOutlined,
  ExclamationCircleOutlined,
  ThunderboltOutlined,
  StarOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from "@ant-design/icons";
import UploadModal from "../UploadModal";

const { Sider, Content } = Layout;

/** 作业管理侧边栏菜单配置（key 即路由路径，供导航与选中态复用） */
const menuItems = [
  { key: "/assignments/records", icon: <FileTextOutlined />, label: "作业记录" },
  { key: "/assignments/error-redo", icon: <ExclamationCircleOutlined />, label: "错题归纳" },
  { key: "/assignments/ai-challenge", icon: <ThunderboltOutlined />, label: "繁星驱动" },
  { key: "/assignments/favorites", icon: <StarOutlined />, label: "我的收藏" },
];

export default function AssignmentLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);

  // 根据当前路由计算选中菜单：子页面路径（如 /assignments/records/123）也能命中父级菜单项
  const selectedKey =
    menuItems.find(
      (item) =>
        location.pathname === item.key || location.pathname.startsWith(`${item.key}/`)
    )?.key ?? menuItems[0].key;

  /** 上传成功后跳转到作业详情页 */
  const handleUploadSuccess = (assignmentId: number) => {
    setUploadOpen(false);
    navigate(`/assignments/${assignmentId}`);
  };

  return (
    <Layout style={{ background: "#f5f7fa", minHeight: "100vh", padding: 16 }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        trigger={null} // 禁用 antd 底部默认触发器，改用自定义的（放在侧边栏最底部）
        width={220}
        style={{
          background: "#fff",
          position: "sticky",
          top: 16,
          height: "calc(100vh - 32px)", // 与四周 16px 留白对齐，滚动时保持悬浮
          alignSelf: "flex-start",
          borderRadius: 12, // 圆角矩形外观
          overflow: "hidden", // 保证顶部渐变/底部按钮跟随圆角裁剪
          boxShadow: "0 2px 8px rgba(0, 0, 0, 0.06)", // 轻微阴影增加悬浮层次
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* 上传作业按钮：独立放置在侧边栏顶部，与下方子板块分隔 */}
        <div style={{ flexShrink: 0, padding: collapsed ? "16px 8px" : "16px 16px 0" }}>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            block
            size="large"
            onClick={() => setUploadOpen(true)}
            style={{
              height: 44,
              borderRadius: 8,
              fontWeight: 600,
              fontSize: 15,
            }}
          >
            {!collapsed && "上传作业"}
          </Button>
        </div>

        {/* 分割线：区分上传按钮与下方功能导航 */}
        <Divider style={{ margin: "12px 16px", minWidth: "auto" }} />

        {/* 功能导航菜单：定制选中态为圆角高亮块，视觉上更精致 */}
        <ConfigProvider
          theme={{
            components: {
              Menu: {
                itemBorderRadius: 8, // 选中项圆角
                itemMarginInline: 8, // 菜单项左右留白，让高亮块不贴边
                itemHeight: 44,
                itemSelectedBg: "#e6f4ff", // 选中背景（主题蓝浅色）
                itemSelectedColor: "#1677ff", // 选中文字（主题蓝）
              },
            },
          }}
        >
          <Menu
            mode="inline"
            selectedKeys={[selectedKey]}
            items={menuItems}
            onClick={({ key }) => navigate(key)}
            style={{ borderInlineEnd: "none", padding: "4px 0", flex: 1, overflow: "auto" }}
          />
        </ConfigProvider>

        {/* 底部折叠按钮区 */}
        <div style={{ flexShrink: 0, padding: 12, borderTop: "1px solid #f0f0f0" }}>
          <Button
            block
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
          >
            {!collapsed && "收起菜单"}
          </Button>
        </div>
      </Sider>
      <Content style={{ padding: "0 16px", minHeight: 360 }}>
        <Outlet />
      </Content>

      {/* 上传作业弹窗 */}
      <UploadModal open={uploadOpen} onClose={() => setUploadOpen(false)} onSuccess={handleUploadSuccess} />
    </Layout>
  );
}
