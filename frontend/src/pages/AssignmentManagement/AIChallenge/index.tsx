import { useState } from "react";
import { Card, Select, InputNumber, Space, Typography, Pagination, Empty, Spin } from "antd";
import { useQuery } from "@tanstack/react-query";
import { aiQuestionService, type AIQuestionItem } from "../../../services/aiQuestionService";
import { GRADE_OPTIONS, SUBJECT_OPTIONS, SEMESTER_OPTIONS, QUESTION_TYPE_OPTIONS, toSelectOptions } from "../../../utils/filterConfig";
import AIQuestionHistoryCard from "../../../components/AIQuestionHistoryCard";

const DIFFICULTY_OPTIONS = [
  { value: "easy", label: "基础" },
  { value: "medium", label: "中等" },
  { value: "hard", label: "拔高" },
];

export default function AIChallenge() {
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({
    grade: "",
    subject: "",
    semester: "",
    question_type: "",
    difficulty: "",
    score_rate_min: null as number | null,
    score_rate_max: null as number | null,
  });

  const updateFilter = (key: string, value: unknown) => {
    setFilters((f) => ({ ...f, [key]: value }));
    setPage(1);
  };

  const { data, isLoading } = useQuery({
    queryKey: ["aiQuestions", page, filters],
    queryFn: () =>
      aiQuestionService.list({
        page,
        page_size: 10,
        ...(filters.grade && { grade: filters.grade }),
        ...(filters.subject && { subject: filters.subject }),
        ...(filters.semester && { semester: filters.semester }),
        ...(filters.question_type && { question_type: filters.question_type }),
        ...(filters.difficulty && { difficulty: filters.difficulty }),
        ...(filters.score_rate_min != null && { score_rate_min: filters.score_rate_min }),
        ...(filters.score_rate_max != null && { score_rate_max: filters.score_rate_max }),
      }),
  });

  return (
    <Card>
      <Typography.Title level={4}>繁星驱动</Typography.Title>
      <Space style={{ marginBottom: 16 }} wrap>
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
        <Select
          placeholder="难度"
          allowClear
          style={{ width: 100 }}
          value={filters.difficulty || undefined}
          onChange={(v) => updateFilter("difficulty", v || "")}
          options={DIFFICULTY_OPTIONS}
        />
        <Space size={4}>
          <span style={{ fontSize: 13, color: "#666" }}>得分率</span>
          <InputNumber
            placeholder="最低"
            min={0}
            max={1}
            step={0.1}
            style={{ width: 80 }}
            value={filters.score_rate_min}
            onChange={(v) => updateFilter("score_rate_min", v)}
          />
          <span style={{ color: "#999" }}>~</span>
          <InputNumber
            placeholder="最高"
            min={0}
            max={1}
            step={0.1}
            style={{ width: 80 }}
            value={filters.score_rate_max}
            onChange={(v) => updateFilter("score_rate_max", v)}
          />
        </Space>
        {/* 注意：没有"搜索作业名称"筛选项 */}
      </Space>

      {isLoading ? (
        <Spin style={{ display: "block", margin: "40px auto" }} />
      ) : data?.items?.length ? (
        <>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {data.items.map((item: AIQuestionItem) => (
              <AIQuestionHistoryCard key={item.id} item={item} />
            ))}
          </Space>
          <Pagination
            current={page}
            pageSize={10}
            total={data?.total || 0}
            onChange={setPage}
            style={{ marginTop: 16, textAlign: "right" }}
            showTotal={(total) => `共 ${total} 条`}
          />
        </>
      ) : (
        <Empty description="暂无AI生成题目记录" />
      )}
    </Card>
  );
}
