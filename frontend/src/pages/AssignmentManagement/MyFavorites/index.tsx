import { useEffect, useState } from "react";
import { Button, Card, message, Select, Space, Typography, Pagination, Empty, Spin, Tag } from "antd";
import { EditOutlined, UploadOutlined } from "@ant-design/icons";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { favoriteService, type FavoriteUnion } from "../../../services/favoriteService";
import {
  GRADE_OPTIONS, SUBJECT_OPTIONS, SEMESTER_OPTIONS,
  QUESTION_TYPE_OPTIONS, toSelectOptions,
} from "../../../utils/filterConfig";
import ErrorQuestionCard from "../../../components/ErrorQuestionCard";
import AIQuestionHistoryCard from "../../../components/AIQuestionHistoryCard";
import QuestionEditModal from "./QuestionEditModal";
import UploadQuestionModal from "./UploadQuestionModal";

/** 题目来源筛选选项（值需与后端 /favorites source 参数一致） */
const SOURCE_OPTIONS = [
  { label: "错题", value: "error" },
  { label: "AI 题", value: "ai" },
  { label: "自有试题", value: "upload" },
];

/**
 * 来源标签渲染：错题=蓝、自有试题=绿、AI 题=紫。
 * 上传转录的题目与 AI 题同为 item_type="ai"，靠 source 区分展示。
 */
function SourceTag({ itemType, source }: { itemType: string; source?: string }) {
  if (itemType === "error") {
    return <Tag color="blue">错题</Tag>;
  }
  if (source === "upload") {
    return <Tag color="green">自有试题</Tag>;
  }
  return <Tag color="purple">AI 题</Tag>;
}

/**
 * 我的收藏：错题 / AI 题 / 自有试题（上传转录）混排展示（按收藏时间倒序），
 * 支持来源/年级/学期/科目/题型筛选，取消收藏后卡片即时移除。
 */
export default function MyFavorites() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({
    source: "",
    grade: "",
    subject: "",
    semester: "",
    question_type: "",
  });
  // 本地移除的收藏记录 id 集合：取消收藏时 UI 即时反馈，refetch 完成后自动清空
  const [removedIds, setRemovedIds] = useState<number[]>([]);
  // 编辑队列：上传转录完成的题逐个检查；卡片点"编辑"时队列只含该条
  const [editQueue, setEditQueue] = useState<FavoriteUnion[]>([]);
  // 编辑队列总题数（upload 时定为 entries.length，之后不再变动——
  // 队列逐题弹出时配合 editQueue.length 计算"当前第几题"，保存/取消都算已检查一道）
  const [editTotal, setEditTotal] = useState(0);
  // 当前正在编辑的收藏条目（取队列首项；空队列表示弹窗关闭）
  const editEntry = editQueue[0] ?? null;
  // 上传试题弹窗开关
  const [uploadOpen, setUploadOpen] = useState(false);
  const queryClient = useQueryClient();

  const updateFilter = (key: string, value: unknown) => {
    setFilters((f) => ({ ...f, [key]: value }));
    setPage(1);
  };

  const { data, isLoading } = useQuery({
    queryKey: ["favorites", page, filters],
    queryFn: () =>
      favoriteService.list({
        page,
        page_size: 10,
        ...(filters.source && { source: filters.source }),
        ...(filters.grade && { grade: filters.grade }),
        ...(filters.subject && { subject: filters.subject }),
        ...(filters.semester && { semester: filters.semester }),
        ...(filters.question_type && { question_type: filters.question_type }),
      }),
  });

  // refetch 完成后新数据已不含已删项，清空本地移除集合（避免双重扣减 total）
  useEffect(() => {
    setRemovedIds([]);
  }, [data]);

  /** 卡片收藏状态切换回调：取消收藏时本地即时移除 + 刷新相关列表缓存（跨页星标同步） */
  const handleToggleFavorite = (entryId: number) => (nowFavorited: boolean) => {
    if (!nowFavorited) {
      setRemovedIds((prev) => [...prev, entryId]);
      queryClient.invalidateQueries({ queryKey: ["favorites"] });
      queryClient.invalidateQueries({ queryKey: ["errorQuestions"] });
      queryClient.invalidateQueries({ queryKey: ["aiQuestions"] });
    }
  };

  /** 编辑保存成功：弹出下一题（若有） + 刷新所有展示题目内容的列表缓存（收藏页/错题重做/AI 挑战/作业详情） */
  const handleSaved = () => {
    setEditQueue((q) => q.slice(1));
    queryClient.invalidateQueries({ queryKey: ["favorites"] });
    queryClient.invalidateQueries({ queryKey: ["errorQuestions"] });
    queryClient.invalidateQueries({ queryKey: ["aiQuestions"] });
    queryClient.invalidateQueries({ queryKey: ["assignment"] });
    queryClient.invalidateQueries({ queryKey: ["assignments"] });
  };

  /** 上传转录完成：关闭上传弹窗 → 打开编辑弹窗检查第一题 + 刷新收藏/AI 题列表缓存 */
  const handleUploaded = (entries: FavoriteUnion[]) => {
    setUploadOpen(false);
    setEditQueue(entries);
    setEditTotal(entries.length); // 队列总题数：弹窗标题/保存提示显示"第 x/N 题"
    queryClient.invalidateQueries({ queryKey: ["favorites"] });
    queryClient.invalidateQueries({ queryKey: ["aiQuestions"] });
    message.success(`已转录 ${entries.length} 道题，请检查题目内容`);
  };

  const items = (data?.items ?? []).filter((it) => !removedIds.includes(it.favorite_id));
  const total = Math.max(0, (data?.total ?? 0) - removedIds.length);

  return (
    <Card>
      <Typography.Title level={4}>我的收藏</Typography.Title>
      {/* flex 布局（不用 Space：antd Space 会包一层 .ant-space-item，子元素上的 marginLeft:auto 不生效）：
          筛选器靠左排列，上传试题按钮以 marginLeft:auto 推到最右侧 */}
      <div
        style={{
          marginBottom: 16,
          display: "flex",
          flexWrap: "wrap",
          gap: 8,
          alignItems: "center",
        }}
      >
        <Select
          placeholder="题目来源"
          allowClear
          style={{ width: 110 }}
          value={filters.source || undefined}
          onChange={(v) => updateFilter("source", v || "")}
          options={SOURCE_OPTIONS}
        />
        <Select
          placeholder="年级"
          allowClear
          style={{ width: 100 }}
          value={filters.grade || undefined}
          onChange={(v) => updateFilter("grade", v || "")}
          options={toSelectOptions(GRADE_OPTIONS)}
        />
        <Select
          placeholder="科目"
          allowClear
          style={{ width: 100 }}
          value={filters.subject || undefined}
          onChange={(v) => updateFilter("subject", v || "")}
          options={toSelectOptions(SUBJECT_OPTIONS)}
        />
        <Select
          placeholder="学期"
          allowClear
          style={{ width: 120 }}
          value={filters.semester || undefined}
          onChange={(v) => updateFilter("semester", v || "")}
          options={toSelectOptions(SEMESTER_OPTIONS)}
        />
        <Select
          placeholder="题型"
          allowClear
          style={{ width: 110 }}
          value={filters.question_type || undefined}
          onChange={(v) => updateFilter("question_type", v || "")}
          options={toSelectOptions(QUESTION_TYPE_OPTIONS)}
        />
        {/* 上传试题：置于筛选栏最右侧 + 放大按钮，上传转录的题目标记为"自有试题" */}
        <Button
          type="primary"
          size="large"
          icon={<UploadOutlined />}
          style={{ marginLeft: "auto", height: 44, fontSize: 16, paddingInline: 24 }}
          onClick={() => setUploadOpen(true)}
          title="上传 Word/PDF/图片试卷，自动转录题目并标注知识点"
        >
          上传试题
        </Button>
      </div>

      {isLoading ? (
        <Spin style={{ display: "block", margin: "40px auto" }} />
      ) : items.length ? (
        <>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {items.map((entry: FavoriteUnion) => (
              <div key={entry.favorite_id}>
                {/* 来源标签 + 年级/科目 + 收藏时间 + 编辑按钮 */}
                <Space size={8} style={{ marginBottom: 4 }}>
                  <SourceTag itemType={entry.item_type} source={entry.source} />
                  {/* 年级/科目标签：有值才显示（错题来自作业元数据，AI 题回落到原题） */}
                  {entry.question.grade && <Tag color="cyan">{entry.question.grade}</Tag>}
                  {entry.question.subject && <Tag color="geekblue">{entry.question.subject}</Tag>}
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    收藏于 {new Date(entry.favorited_at).toLocaleString("zh-CN")}
                  </Typography.Text>
                  <Button
                    size="small"
                    type="primary"
                    icon={<EditOutlined />}
                    onClick={() => {
                      setEditTotal(1); // 单题编辑：弹窗不显示队列进度
                      setEditQueue([entry]);
                    }}
                    title="编辑题目内容（对照原图修正缺失/错误的转录）"
                  >
                    编辑
                  </Button>
                </Space>
                {entry.item_type === "error" ? (
                  /* 收藏页只展示结构化转录后的题目内容：隐藏原作答图像（hideImage）、
                     隐藏作答痕迹（hideStudentAnswer）；不传 hideAnswer
                     （每题有"查看答案"按钮展示正确答案与解析） */
                  <ErrorQuestionCard
                    item={entry.question}
                    isFavorited
                    onToggleFavorite={handleToggleFavorite(entry.favorite_id)}
                    hideStudentAnswer
                    hideImage
                  />
                ) : (
                  <AIQuestionHistoryCard
                    item={entry.question}
                    isFavorited
                    onToggleFavorite={handleToggleFavorite(entry.favorite_id)}
                    hideStudentAnswer
                    hideImage
                  />
                )}
              </div>
            ))}
          </Space>
          <Pagination
            current={page}
            pageSize={10}
            total={total}
            onChange={setPage}
            style={{ marginTop: 16, textAlign: "right" }}
            showTotal={(t) => `共 ${t} 条`}
          />
        </>
      ) : (
        <Empty description="暂无收藏" />
      )}

      {/* 上传试题弹窗（表单 + 转录任务轮询，完成后进入编辑队列检查） */}
      <UploadQuestionModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onSuccess={handleUploaded}
      />

      {/* 题目编辑三栏弹窗（key 按条目切换，每次打开重新初始化；
          编辑队列逐个检查：取消/保存后弹出下一题。
          queueIndex/queueTotal：editTotal 恒定 = 队列总题数，editQueue.length 随弹出递减，
          "editTotal - editQueue.length + 1" 即当前第几题（保存/取消都算已检查一道）） */}
      <QuestionEditModal
        key={editEntry?.favorite_id ?? "none"}
        open={!!editEntry}
        entry={editEntry}
        queueIndex={editTotal - editQueue.length + 1}
        queueTotal={editTotal}
        onCancel={() => setEditQueue((q) => q.slice(1))}
        onSaved={handleSaved}
      />
    </Card>
  );
}
