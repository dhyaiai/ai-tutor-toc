/**
 * 账号设置页面（仅超级管理员可访问）
 *
 * 功能：
 * - 添加用户：输入手机号（必填）+ 用户名（选填）创建账号，初始密码由后端随机生成（仅返回一次），角色 = 普通用户
 * - 用户列表：展示所有已创建账号（用户名/手机号/角色/创建时间）
 * - 编辑用户：修改手机号、用户名、重置密码、切换角色（普通用户 ↔ 超级管理员）
 * - 删除用户：级联清理该用户全部数据（作业、AI 题目、会话、测评记录等）
 *
 * 注册功能已取消，这里是系统唯一的开户入口。
 * 页面级守卫只做 UX 提示，真正的权限控制由后端 get_current_admin（403）兜底。
 */

import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Card,
  Divider,
  Form,
  Input,
  Button,
  Table,
  Typography,
  Tag,
  message,
  Modal,
  Radio,
  Popconfirm,
  Tooltip,
} from "antd";
import { UserAddOutlined, EditOutlined, DeleteOutlined } from "@ant-design/icons";
import { userService, type UserInfo, type UserUpdateData } from "../../services/userService";
import { useAuth } from "../../hooks/useAuth";

const { Title, Text } = Typography;

/** 角色展示：admin=超级管理员，其余一律按普通用户展示 */
const renderRole = (role: string) =>
  role === "admin" ? <Tag color="gold">超级管理员</Tag> : <Tag color="blue">普通用户</Tag>;

export default function AccountManagement() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [editForm] = Form.useForm();
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // 编辑弹窗状态
  const [editingUser, setEditingUser] = useState<UserInfo | null>(null);
  const [editing, setEditing] = useState(false);
  // 删除中的用户 id（用于按钮 loading）
  const [deletingId, setDeletingId] = useState<number | null>(null);

  /** 页面级守卫：非超级管理员访问时提示并跳回作业管理 */
  useEffect(() => {
    if (user && user.role !== "admin") {
      message.warning("无权限访问该页面");
      navigate("/assignments", { replace: true });
    }
  }, [user, navigate]);

  /** 加载用户列表 */
  useEffect(() => {
    if (user?.role !== "admin") return;
    let cancelled = false;
    (async () => {
      setLoadingList(true);
      try {
        const users = await userService.listUsers();
        if (!cancelled) setUsers(users);
      } catch {
        if (!cancelled) message.error("加载用户列表失败");
      } finally {
        if (!cancelled) setLoadingList(false);
      }
    })();
    return () => { cancelled = true; };
  }, [user?.role]);

  /** 添加用户：手机号必填，用户名选填，初始密码由后端随机生成（仅返回一次） */
  const handleCreate = async (values: { phone: string; username?: string }) => {
    setSubmitting(true);
    try {
      const result = await userService.createUser(values.phone, values.username || null);
      const displayName = values.username || values.phone;
      // 使用 Modal 展示初始密码（不能用 toast，会自动消失导致密码丢失）
      Modal.success({
        title: "用户创建成功",
        content: (
          <div>
            <p>账号：{displayName}（{values.phone}）</p>
            <p>
              初始密码：<strong style={{ fontSize: 16, letterSpacing: 2 }}>{result.initial_password}</strong>
            </p>
            <p style={{ fontSize: 12, color: "#888" }}>
              此密码仅显示一次，请转告用户尽快登录修改
            </p>
          </div>
        ),
        okText: "复制密码并关闭",
        onOk: () => {
          navigator.clipboard?.writeText(result.initial_password).catch(() => {});
        },
      });
      form.resetFields();
      // 创建成功后刷新列表，新用户出现在最前面
      setUsers(await userService.listUsers());
    } catch (err: unknown) {
      // 后端错误消息（409 手机号已注册 / 422 格式不正确）为中文，直接展示
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        "创建用户失败";
      message.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  /** 打开编辑弹窗：回填当前值（角色默认选中当前角色） */
  const openEdit = (record: UserInfo) => {
    setEditingUser(record);
    editForm.setFieldsValue({
      phone: record.phone,
      username: record.username || "",
      password: "",
      role: record.role,
    });
  };

  const closeEdit = () => {
    setEditingUser(null);
    editForm.resetFields();
  };

  /** 提交编辑：改手机号 / 用户名 / 重置密码 / 切换角色 */
  const handleEditSubmit = async () => {
    if (!editingUser) return;
    try {
      const values = await editForm.validateFields();
      const payload: UserUpdateData = {
        phone: values.phone,
        username: values.username || null,
        role: values.role,
      };
      // 密码留空表示不修改
      if (values.password) payload.password = values.password;
      setEditing(true);
      await userService.updateUser(editingUser.id, payload);
      message.success("用户信息已更新");
      closeEdit();
      setUsers(await userService.listUsers());
    } catch (err: unknown) {
      // validateFields 校验失败（err 含 errorFields）时不弹错误，表单已显示提示
      const e = err as { errorFields?: unknown; response?: { data?: { detail?: string } } };
      if (e?.errorFields) return;
      message.error(e?.response?.data?.detail || "更新用户失败");
    } finally {
      setEditing(false);
    }
  };

  /** 删除用户：级联清理该用户全部数据 */
  const handleDelete = async (record: UserInfo) => {
    setDeletingId(record.id);
    try {
      await userService.deleteUser(record.id);
      const displayName = record.username || record.phone;
      message.success(`已删除用户 ${displayName} 及其全部数据`);
      setUsers(await userService.listUsers());
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        "删除用户失败";
      message.error(msg);
    } finally {
      setDeletingId(null);
    }
  };

  /** 格式化创建时间 */
  const formatTime = (value: string) =>
    value ? new Date(value).toLocaleString("zh-CN") : "——";

  return (
    <div style={{ maxWidth: 960, margin: "0 auto" }}>
      <Title level={4}>账号设置</Title>
      <Divider />

      {/* 添加用户 */}
      <Card title="添加用户" style={{ marginBottom: 24 }}>
        <Form form={form} layout="inline" onFinish={handleCreate}>
          <Form.Item
            name="phone"
            rules={[
              { required: true, message: "请输入手机号" },
              { pattern: /^1[3-9]\d{9}$/, message: "请输入正确的中国大陆手机号（1 开头共 11 位数字）" },
            ]}
          >
            <Input
              placeholder="手机号（必填）"
              maxLength={11}
              style={{ width: 200 }}
              onInput={(e) => {
                // 统一清洗非数字字符（覆盖粘贴/自动填充等场景）
                const input = e.target as HTMLInputElement;
                const cleaned = input.value.replace(/\D/g, "");
                if (cleaned !== input.value) {
                  input.value = cleaned;
                }
              }}
            />
          </Form.Item>
          <Form.Item
            name="username"
          >
            <Input
              placeholder="用户名（选填，不填默认显示手机号）"
              maxLength={64}
              style={{ width: 260 }}
            />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={submitting} icon={<UserAddOutlined />}>
              添加用户
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {/* 用户列表 */}
      <Card title={`用户列表（${users.length}）`}>
        <Table<UserInfo>
          rowKey="id"
          loading={loadingList}
          dataSource={users}
          pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 个用户` }}
          columns={[
            {
              title: "用户名",
              dataIndex: "username",
              key: "username",
              render: (v: string | null, record: UserInfo) => (
                <Text strong>{v || record.phone}</Text>
              ),
            },
            {
              title: "手机号",
              dataIndex: "phone",
              key: "phone",
              width: 150,
              render: (v: string) => <Text type="secondary">{v}</Text>,
            },
            {
              title: "角色",
              dataIndex: "role",
              key: "role",
              width: 140,
              render: renderRole,
            },
            {
              title: "创建时间",
              dataIndex: "created_at",
              key: "created_at",
              width: 200,
              render: formatTime,
            },
            {
              title: "操作",
              key: "action",
              width: 160,
              render: (_, record) => {
                // 当前登录的管理员不能编辑/删除自己，避免手滑降级或删除导致系统失管
                const isSelf = record.id === user?.id;
                return (
                  <>
                    <Tooltip title={isSelf ? "不能操作当前登录的管理员账号" : "编辑用户信息"}>
                      <Button
                        type="link"
                        size="small"
                        icon={<EditOutlined />}
                        disabled={isSelf}
                        onClick={() => openEdit(record)}
                      >
                        编辑
                      </Button>
                    </Tooltip>
                    <Popconfirm
                      title="确认删除该用户？"
                      description="将永久删除该用户的全部数据（作业、AI 题目、会话、测评记录等），且不可恢复。"
                      okText="删除"
                      okButtonProps={{ danger: true }}
                      cancelText="取消"
                      onConfirm={() => handleDelete(record)}
                    >
                      <Button
                        type="link"
                        size="small"
                        danger
                        icon={<DeleteOutlined />}
                        loading={deletingId === record.id}
                        disabled={isSelf}
                      >
                        删除
                      </Button>
                    </Popconfirm>
                  </>
                );
              },
            },
          ]}
        />
      </Card>

      {/* 编辑用户弹窗 */}
      <Modal
        title={`编辑用户：${editingUser?.username || editingUser?.phone || ""}`}
        open={editingUser !== null}
        onOk={handleEditSubmit}
        onCancel={closeEdit}
        confirmLoading={editing}
        okText="保存"
        cancelText="取消"
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="phone"
            label="手机号（登录账号）"
            rules={[
              { required: true, message: "请输入手机号" },
              { pattern: /^1[3-9]\d{9}$/, message: "请输入正确的中国大陆手机号（1 开头共 11 位数字）" },
            ]}
          >
            <Input maxLength={11} placeholder="请输入中国大陆手机号" />
          </Form.Item>
          <Form.Item
            name="username"
            label="用户名（显示名称，留空则显示手机号）"
          >
            <Input maxLength={64} placeholder="留空则显示手机号" />
          </Form.Item>
          <Form.Item
            name="password"
            label="重置密码（可选）"
            rules={[
              { min: 8, message: "密码至少 8 位" },
              { pattern: /(?=.*[A-Za-z])(?=.*\d)/, message: "密码必须包含字母和数字" },
            ]}
          >
            <Input.Password placeholder="留空则不修改密码" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true, message: "请选择角色" }]}>
            <Radio.Group>
              <Radio value="user">普通用户</Radio>
              <Radio value="admin">超级管理员</Radio>
            </Radio.Group>
          </Form.Item>
          <Text type="secondary" style={{ fontSize: 12 }}>
            注意：系统必须保留至少一个超级管理员，且不能修改自己当前账号的角色。
          </Text>
        </Form>
      </Modal>
    </div>
  );
}
