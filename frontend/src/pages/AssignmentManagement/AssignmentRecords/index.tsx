import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, Table, Select, Tag, Space, Typography, Button, message, Dropdown, Modal, Form, Input } from "antd";
import { EyeOutlined, MoreOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { assignmentService, type AssignmentListItem } from "../../../services/assignmentService";
import { ASSIGNMENT_STATUS_MAP } from "../../../utils/constants";
import { GRADE_OPTIONS, SUBJECT_OPTIONS, SEMESTER_OPTIONS, toSelectOptions } from "../../../utils/filterConfig";
import { formatDate } from "../../../utils/helpers";

export default function AssignmentRecords() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ grade: "", subject: "", semester: "" });
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editingRecord, setEditingRecord] = useState<AssignmentListItem | null>(null);
  const [editForm] = Form.useForm();

  const { data, isLoading } = useQuery({
    queryKey: ["assignments", page, filters],
    queryFn: () =>
      assignmentService.list({
        page,
        page_size: 10,
        ...(filters.grade && { grade: filters.grade }),
        ...(filters.subject && { subject: filters.subject }),
        ...(filters.semester && { semester: filters.semester }),
      }),
  });

  const updateFilter = (key: string, value: string) => {
    setFilters((f) => ({ ...f, [key]: value }));
    setPage(1);
  };

  const deleteMutation = useMutation({
    mutationFn: (id: number) => assignmentService.delete(id),
    onSuccess: () => {
      message.success("作业已删除");
      queryClient.invalidateQueries({ queryKey: ["assignments"] });
      queryClient.invalidateQueries({ queryKey: ["analytics"] });
    },
    onError: (err: any) => {
      message.error("删除失败: " + (err?.response?.data?.detail || err?.message || "未知错误"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, ...params }: { id: number; name?: string; grade?: string; subject?: string; semester?: string; month?: string }) =>
      assignmentService.update(id, params),
    onSuccess: (_, variables) => {
      message.success("作业信息已更新");
      setEditModalOpen(false);
      setEditingRecord(null);
      queryClient.invalidateQueries({ queryKey: ["assignments"] });
      // 同步更新作业详情（如果正在查看）和学情分析数据
      queryClient.invalidateQueries({ queryKey: ["assignment", variables.id] });
      queryClient.invalidateQueries({ queryKey: ["analytics"] });
    },
    onError: (err: any) => {
      message.error("更新失败: " + (err?.response?.data?.detail || err?.message || "未知错误"));
    },
  });

  const openEditModal = (record: AssignmentListItem) => {
    setEditingRecord(record);
    editForm.setFieldsValue({
      name: record.name,
      grade: record.grade,
      subject: record.subject,
      semester: record.semester,
      month: record.month,
    });
    setEditModalOpen(true);
  };

  const columns = [
    { title: "作业名称", dataIndex: "name", key: "name", ellipsis: true },
    { title: "年级", dataIndex: "grade", key: "grade", width: 80 },
    { title: "科目", dataIndex: "subject", key: "subject", width: 80 },
    { title: "学期", dataIndex: "semester", key: "semester", width: 100 },
    { title: "月份", dataIndex: "month", key: "month", width: 80 },
    { title: "题目数", dataIndex: "question_count", key: "question_count", width: 80 },
    { title: "错题数", dataIndex: "error_count", key: "error_count", width: 80 },
    {
      title: "总分",
      dataIndex: "total_score",
      key: "total_score",
      width: 80,
      render: (v: number | null) => (v != null ? v : "-"),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (v: string) => {
        const cfg = ASSIGNMENT_STATUS_MAP[v] || { color: "default", label: v };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: "上传时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 120,
      render: (v: string) => formatDate(v, true),
    },
    {
      title: "操作",
      key: "action",
      width: 120,
      render: (_: unknown, record: AssignmentListItem) => (
        <Space>
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={() => navigate(`/assignments/${record.id}`)}
          >
            查看
          </Button>
          <Dropdown
            menu={{
              items: [
                {
                  key: "edit",
                  label: "编辑",
                  onClick: () => openEditModal(record),
                },
                {
                  key: "delete",
                  label: "删除",
                  danger: true,
                  onClick: () => {
                    Modal.confirm({
                      title: "确认删除此作业？",
                      content: "删除后不可恢复，关联的题目和分析数据将一并删除",
                      okText: "确认删除",
                      okType: "danger",
                      cancelText: "取消",
                      onOk: () => deleteMutation.mutate(record.id),
                    });
                  },
                },
              ],
            }}
            trigger={["click"]}
          >
            <Button type="link" icon={<MoreOutlined />}>
              更多
            </Button>
          </Dropdown>
        </Space>
      ),
    },
  ];

  return (
    <Card>
      <Typography.Title level={4}>作业记录</Typography.Title>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder="选择年级"
          allowClear
          style={{ width: 120 }}
          onChange={(v) => updateFilter("grade", v || "")}
          options={toSelectOptions(GRADE_OPTIONS)}
        />
        <Select
          placeholder="选择科目"
          allowClear
          style={{ width: 120 }}
          onChange={(v) => updateFilter("subject", v || "")}
          options={toSelectOptions(SUBJECT_OPTIONS)}
        />
        <Select
          placeholder="选择学期"
          allowClear
          style={{ width: 140 }}
          onChange={(v) => updateFilter("semester", v || "")}
          options={toSelectOptions(SEMESTER_OPTIONS)}
        />
      </Space>
      <Table
        columns={columns}
        dataSource={data?.items || []}
        rowKey="id"
        loading={isLoading}
        pagination={{
          current: page,
          pageSize: 10,
          total: data?.total || 0,
          onChange: setPage,
          showTotal: (total) => `共 ${total} 条`,
        }}
      />

      {/* 编辑弹窗 */}
      <Modal
        title="编辑作业信息"
        open={editModalOpen}
        onCancel={() => { setEditModalOpen(false); setEditingRecord(null); }}
        onOk={() => editForm.submit()}
        confirmLoading={updateMutation.isPending}
        destroyOnClose
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={(values) => {
            if (!editingRecord) return;
            updateMutation.mutate({ id: editingRecord.id, ...values });
          }}
        >
          <Form.Item name="name" label="作业名称" rules={[{ required: true, message: "请输入作业名称" }]}>
            <Input />
          </Form.Item>
          <Space size="middle">
            <Form.Item name="grade" label="年级" rules={[{ required: true }]}>
              <Select style={{ width: 140 }} options={toSelectOptions(GRADE_OPTIONS)} />
            </Form.Item>
            <Form.Item name="subject" label="科目" rules={[{ required: true }]}>
              <Select style={{ width: 140 }} options={toSelectOptions(SUBJECT_OPTIONS)} />
            </Form.Item>
          </Space>
          <Space size="middle">
            <Form.Item name="semester" label="学期" rules={[{ required: true }]}>
              <Select style={{ width: 140 }} options={toSelectOptions(SEMESTER_OPTIONS)} />
            </Form.Item>
            <Form.Item name="month" label="月份" rules={[{ required: true }]}>
              <Input style={{ width: 140 }} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </Card>
  );
}
