import { useState } from "react";
import { useNavigate, useSearchParams, useLocation } from "react-router-dom";
import { Card, Select, Space, Typography, Button, message, Dropdown, Modal, Form, Input, Pagination } from "antd";
import { EyeOutlined, MoreOutlined, ReloadOutlined, FileTextOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { assignmentService, type AssignmentListItem } from "../../../services/assignmentService";
import { ASSIGNMENT_STATUS_MAP } from "../../../utils/constants";
import { GRADE_OPTIONS, SUBJECT_OPTIONS, SEMESTER_OPTIONS, toSelectOptions } from "../../../utils/filterConfig";
import { formatDate } from "../../../utils/helpers";
import "./index.css"; // 页面专属样式（仅作用于 .assignment-records 作用域）

/** 校验 URL 筛选参数是否在合法选项内（防止手改地址出现非法值，Select 显示异常） */
function isOption(v: string | null, options: string[]): v is string {
  return !!v && options.includes(v);
}

export default function AssignmentRecords() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editingRecord, setEditingRecord] = useState<AssignmentListItem | null>(null);
  const [editForm] = Form.useForm();

  /* 筛选条件与分页以 URL query 为唯一事实来源（与 LearningAnalytics 同款模式）：
     - 从作业详情返回/浏览器后退/刷新页面时，query 保留，筛选状态自动恢复；
     - 非法或缺失的值回落为默认（年级/科目/学期为空、页码为 1） */
  const filters = {
    grade: isOption(searchParams.get("grade"), GRADE_OPTIONS) ? searchParams.get("grade")! : "",
    subject: isOption(searchParams.get("subject"), SUBJECT_OPTIONS) ? searchParams.get("subject")! : "",
    semester: isOption(searchParams.get("semester"), SEMESTER_OPTIONS) ? searchParams.get("semester")! : "",
  };
  const pageParam = Number(searchParams.get("page") ?? 1);
  const page = Number.isInteger(pageParam) && pageParam >= 1 ? pageParam : 1;

  /** 将筛选 + 页码写入 URL（replace 不产生历史记录，避免污染返回栈） */
  const writeParams = (grade: string, subject: string, semester: string, page: number) => {
    const params = new URLSearchParams();
    if (grade) params.set("grade", grade);
    if (subject) params.set("subject", subject);
    if (semester) params.set("semester", semester);
    params.set("page", String(page));
    setSearchParams(params, { replace: true });
  };

  const { data, isLoading } = useQuery({
    queryKey: ["assignments", page, filters.grade, filters.subject, filters.semester],
    queryFn: () =>
      assignmentService.list({
        page,
        page_size: 10,
        ...(filters.grade && { grade: filters.grade }),
        ...(filters.subject && { subject: filters.subject }),
        ...(filters.semester && { semester: filters.semester }),
      }),
  });

  /** 更新筛选：写入 URL，页码重置回 1 */
  const updateFilter = (key: "grade" | "subject" | "semester", value: string) => {
    writeParams(
      key === "grade" ? value : filters.grade,
      key === "subject" ? value : filters.subject,
      key === "semester" ? value : filters.semester,
      1
    );
  };

  /* 当前是否有筛选条件（决定是否显示"重置"按钮） */
  const hasFilter = !!(filters.grade || filters.subject || filters.semester);

  /* 一键清空全部筛选并回到第一页 */
  const resetFilters = () => {
    setSearchParams({ page: "1" }, { replace: true });
  };

  const deleteMutation = useMutation({
    mutationFn: (id: number) => assignmentService.delete(id),
    onSuccess: () => {
      message.success("作业已删除");
      // 删除的是当前页最后一条记录时回退一页（A4-5）：
      // 不处理的话会停留在越界空页（该页无数据但 current 超过有效页码）
      if (data?.items?.length === 1 && page > 1) {
        writeParams(filters.grade, filters.subject, filters.semester, page - 1);
      }
      queryClient.invalidateQueries({ queryKey: ["assignments"] });
      queryClient.invalidateQueries({ queryKey: ["analytics"] });
    },
    onError: (err: any) => {
      message.error("删除失败: " + (err?.response?.data?.detail || err?.message || "未知错误"));
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, ...params }: { id: number; name?: string; grade?: string; subject?: string; semester?: string; usage_month?: string }) =>
      assignmentService.update(id, params),
    onSuccess: (_, variables) => {
      message.success("作业信息已更新");
      setEditModalOpen(false);
      setEditingRecord(null);
      queryClient.invalidateQueries({ queryKey: ["assignments"] });
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
      usage_month: record.usage_month,
    });
    setEditModalOpen(true);
  };

  /* 状态列：圆点 + 文字（颜色与 ASSIGNMENT_STATUS_MAP 一一对应，见 index.css） */
  const renderStatus = (v: string) => {
    const cfg = ASSIGNMENT_STATUS_MAP[v] || { color: "default", label: v };
    return (
      <span className="ar-status">
        <span className={`ar-status-dot ar-status-dot-${cfg.color}`} />
        {cfg.label}
      </span>
    );
  };

  return (
    <div className="assignment-records">
      <Card styles={{ body: { padding: "24px 28px" } }}>
        {/* 页面头部：标题 + 副标题 + 右侧当前筛选下的总数 */}
        <div className="ar-header">
          <div className="ar-header-left">
            <Typography.Title level={4}>作业记录</Typography.Title>
          </div>
          <span className="ar-header-count">
            共 <b>{data?.total ?? 0}</b> 份作业
          </span>
        </div>

        {/* 筛选条：标签 + 选择器组合，带一键重置 */}
        <div className="ar-filter-bar">
          <div className="ar-filter-fields">
            <div className="ar-filter-field">
              <span className="ar-filter-label">年级</span>
              <Select
                placeholder="全部"
                allowClear
                value={filters.grade || undefined}
                onChange={(v) => updateFilter("grade", v || "")}
                options={toSelectOptions(GRADE_OPTIONS)}
              />
            </div>
            <div className="ar-filter-field">
              <span className="ar-filter-label">科目</span>
              <Select
                placeholder="全部"
                allowClear
                value={filters.subject || undefined}
                onChange={(v) => updateFilter("subject", v || "")}
                options={toSelectOptions(SUBJECT_OPTIONS)}
              />
            </div>
            <div className="ar-filter-field">
              <span className="ar-filter-label">学期</span>
              <Select
                placeholder="全部"
                allowClear
                value={filters.semester || undefined}
                onChange={(v) => updateFilter("semester", v || "")}
                options={toSelectOptions(SEMESTER_OPTIONS)}
              />
            </div>
            {hasFilter && (
              <Button
                type="text"
                className="ar-filter-reset"
                icon={<ReloadOutlined />}
                onClick={resetFilters}
              >
                重置筛选
              </Button>
            )}
          </div>
        </div>

        {/* 卡片列表 */}
        <div className="ar-card-list">
          {isLoading ? (
            <div className="ar-loading">加载中...</div>
          ) : !data?.items?.length ? (
            <div className="ar-empty">暂无作业记录</div>
          ) : (
            data.items.map((record) => (
              <div className="ar-card" key={record.id}>
                {/* 左侧：正方形分值圆角矩形 */}
                <div className="ar-card-badge">
                  <span className="ar-card-badge-score">{record.total_score ?? "—"}</span>
                  <span className="ar-card-badge-sep">/</span>
                  <span className="ar-card-badge-total">{record.full_total ?? "—"}</span>
                </div>
                {/* 卡片主体 */}
                <div className="ar-card-body">
                  {/* 左侧：信息 */}
                  <div className="ar-card-info">
                    {/* 第一行：作业名称 */}
                    <div className="ar-card-row ar-card-row-title">
                      {/* 跳转详情时带上当前筛选 query，返回记录页时筛选状态保留 */}
                      <span className="ar-card-title" onClick={() => navigate(`/assignments/${record.id}${location.search}`)}>
                        {record.name}
                      </span>
                    </div>
                    {/* 第二行：元信息标签 */}
                    <div className="ar-card-row ar-card-row-meta">
                      <span className="ar-card-meta-tag">{record.grade}</span>
                      <span className="ar-card-meta-tag">{record.subject}</span>
                      <span className="ar-card-meta-tag">{record.semester}</span>
                      <span className="ar-card-meta-tag">{record.usage_month}</span>
                      <span className="ar-card-meta-tag">{record.question_count}题</span>
                    </div>
                    {/* 第三行：状态 */}
                    <div className="ar-card-row ar-card-row-status">
                      {renderStatus(record.status)}
                    </div>
                  </div>
                  {/* 右侧：操作按钮 */}
                  <div className="ar-card-actions">
                    <Button
                      className="ar-action-view"
                      icon={<EyeOutlined />}
                      onClick={() => navigate(`/assignments/${record.id}${location.search}`)}
                    >
                      查看
                    </Button>
                    <Dropdown
                      menu={{
                        items: [
                          {
                            key: "edit",
                            label: "编辑",
                            icon: <FileTextOutlined />,
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
                      <Button className="ar-action-more">
                        更多
                      </Button>
                    </Dropdown>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>

        {/* 分页 */}
        <div className="ar-pagination">
          <Pagination
            current={page}
            pageSize={10}
            total={data?.total || 0}
            onChange={(p) => writeParams(filters.grade, filters.subject, filters.semester, p)}
            showTotal={(total) => `共 ${total} 条`}
          />
        </div>

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
              <Form.Item name="usage_month" label="使用月份" rules={[{ required: true }]}>
                <Input style={{ width: 140 }} placeholder="例如：2026-03" />
              </Form.Item>
            </Space>
          </Form>
        </Modal>
      </Card>
    </div>
  );
}
