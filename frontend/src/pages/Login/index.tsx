import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Card, Form, Input, Button, Typography, message } from "antd";
import { PhoneOutlined, LockOutlined } from "@ant-design/icons";
import { useAuth } from "../../hooks/useAuth";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [loading, setLoading] = useState(false);

  const from = (location.state as { from?: { pathname: string } })?.from?.pathname || "/assignments";

  const handleLogin = async (values: { phone: string; password: string }) => {
    setLoading(true);
    try {
      await login(values.phone, values.password);
      message.success("登录成功");
      navigate(from, { replace: true });
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        "登录失败";
      message.error(msg);
    } finally {
      setLoading(false);
    }
  };

  // 注：注册功能已取消，账号由超级管理员在"账号设置"中创建
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "100vh",
        background: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
      }}
    >
      <Card style={{ width: 400, boxShadow: "0 8px 24px rgba(0,0,0,0.15)" }}>
        <Typography.Title level={3} style={{ textAlign: "center", marginBottom: 24 }}>
          AI 助教系统
        </Typography.Title>
        <Form onFinish={handleLogin} size="large">
          <Form.Item
            name="phone"
            rules={[
              { required: true, message: "请输入账号" },
              // 账号不限定手机号：开户支持管理员创建任意账号（含测试账号），
              // 过度严格的手机号正则会把合法账号挡在门外，导致无法登录
              { pattern: /^[\w@.\-]{3,64}$/, message: "账号格式不正确（3-64位字母/数字/._@-）" },
            ]}
          >
            <Input prefix={<PhoneOutlined />} placeholder="账号 / 手机号" maxLength={64} />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              登录
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
