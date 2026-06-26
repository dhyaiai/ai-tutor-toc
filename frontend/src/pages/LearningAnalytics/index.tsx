import { useState } from "react";
import { Card, Select, Space, Typography, Spin, Row, Col, Statistic, Empty } from "antd";
import { useQuery } from "@tanstack/react-query";
import { analyticsService } from "../../services/analyticsService";
import { GRADE_OPTIONS, SUBJECT_OPTIONS, SEMESTER_OPTIONS, toSelectOptions } from "../../utils/filterConfig";

export default function LearningAnalytics() {
  const [filters, setFilters] = useState({ grade: "", subject: "", semester: "" });

  const { data: overview, isLoading: loadingOverview } = useQuery({
    queryKey: ["analytics-overview", filters.grade, filters.subject],
    queryFn: () =>
      analyticsService.getOverview({
        ...(filters.grade && { grade: filters.grade }),
        ...(filters.subject && { subject: filters.subject }),
      }),
  });

  const { data: trend, isLoading: loadingTrend } = useQuery({
    queryKey: ["analytics-trend", filters],
    queryFn: () =>
      analyticsService.getScoreTrend({
        ...(filters.grade && { grade: filters.grade }),
        ...(filters.subject && { subject: filters.subject }),
        ...(filters.semester && { semester: filters.semester }),
      }),
  });

  const { data: weakness, isLoading: loadingWeakness } = useQuery({
    queryKey: ["analytics-weakness", filters],
    queryFn: () =>
      analyticsService.getWeakness({
        ...(filters.grade && { grade: filters.grade }),
        ...(filters.subject && { subject: filters.subject }),
        ...(filters.semester && { semester: filters.semester }),
        limit: 10,
      }),
  });

  return (
    <div>
      <Card style={{ marginBottom: 16 }}>
        <Space size="middle">
          <Typography.Text strong>筛选条件：</Typography.Text>
          <Select
            placeholder="年级"
            allowClear
            style={{ width: 120 }}
            onChange={(v) => setFilters((f) => ({ ...f, grade: v || "" }))}
            options={toSelectOptions(GRADE_OPTIONS)}
          />
          <Select
            placeholder="科目"
            allowClear
            style={{ width: 120 }}
            onChange={(v) => setFilters((f) => ({ ...f, subject: v || "" }))}
            options={toSelectOptions(SUBJECT_OPTIONS)}
          />
          <Select
            placeholder="学期"
            allowClear
            style={{ width: 160 }}
            onChange={(v) => setFilters((f) => ({ ...f, semester: v || "" }))}
            options={toSelectOptions(SEMESTER_OPTIONS)}
          />
        </Space>
      </Card>

      {/* Overview Stats */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic title="作业总份数" value={overview?.total_assignments || 0} loading={loadingOverview} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="平均分" value={overview?.average_score || 0} precision={1} loading={loadingOverview} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="总题数" value={overview?.total_questions || 0} loading={loadingOverview} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="错误率"
              value={overview ? (overview.error_rate * 100).toFixed(1) + "%" : "0%"}
              loading={loadingOverview}
            />
          </Card>
        </Col>
      </Row>

      {/* Score Trend */}
      <Card title="分数趋势" style={{ marginBottom: 16 }}>
        {loadingTrend ? (
          <Spin />
        ) : trend?.trends?.length ? (
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            {(trend.trends as Array<{ month: string; average_score: number; count: number }>).map((point) => (
              <Card key={point.month} size="small" style={{ width: 160 }}>
                <Statistic
                  title={point.month}
                  value={point.average_score}
                  suffix={`分 / ${point.count}份`}
                  precision={1}
                />
              </Card>
            ))}
          </div>
        ) : (
          <Empty description="暂无趋势数据" />
        )}
      </Card>

      {/* Weak Points */}
      <Card title="薄弱知识点">
        {loadingWeakness ? (
          <Spin />
        ) : weakness?.weak_points?.length ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {(weakness.weak_points as Array<{ knowledge_point: string; error_rate: number; error_count: number }>).map(
              (wp) => (
                <Card key={wp.knowledge_point} size="small" style={{ width: 200 }}>
                  <Statistic
                    title={wp.knowledge_point}
                    value={(wp.error_rate * 100).toFixed(1) + "%"}
                    suffix={`${wp.error_count}题错`}
                  />
                </Card>
              ),
            )}
          </div>
        ) : (
          <Empty description="暂无薄弱知识点数据" />
        )}
      </Card>
    </div>
  );
}
