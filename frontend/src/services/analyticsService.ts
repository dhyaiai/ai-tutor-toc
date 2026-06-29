/**
 * 学情分析 API 服务。
 *
 * 三个子板块对应的 API 调用：
 * - getHomeworkStats     → GET /analytics/homework-stats      (作业统计)
 * - getStudentDashboard  → GET /analytics/student-dashboard   (学生学期看板)
 * - getKnowledgeHeatmap  → GET /analytics/knowledge-heatmap   (知识点热力图)
 */

import api from "./api";

// ==================== 类型定义 ====================

/** 各科目作业数量统计 */
export interface SubjectStat {
  subject: string;
  count: number;
}

/** 作业统计响应 */
export interface HomeworkStatsResponse {
  total: number;
  subject_stats: SubjectStat[];
}

/** 学生学期看板单条数据 */
export interface DashboardItem {
  id: number;
  name: string;
  grade: string;
  subject: string;
  semester: string;
  created_at: string;
  score_rate: number; // 0~1
}

/** 学生学期看板响应 */
export interface DashboardResponse {
  items: DashboardItem[];
}

/** 知识点热力图单条数据 */
export interface KnowledgeHeatmapItem {
  knowledge_point: string;
  frequency: number;
  score_rate: number; // 0~1
}

/** 知识点热力图响应 */
export interface KnowledgeHeatmapResponse {
  items: KnowledgeHeatmapItem[];
}

// ==================== API 方法 ====================

export const analyticsService = {
  /** 子板块1：作业统计 — 按科目统计作业数量 */
  async getHomeworkStats(params?: {
    grade?: string;
    semester?: string;
  }): Promise<HomeworkStatsResponse> {
    const { data } = await api.get("/analytics/homework-stats", { params });
    return data;
  },

  /** 子板块2：学生学期看板 — 每份作业的得分率（按时间排序） */
  async getStudentDashboard(params?: {
    grade?: string;
    subject?: string;
    semester?: string;
  }): Promise<DashboardResponse> {
    const { data } = await api.get("/analytics/student-dashboard", { params });
    return data;
  },

  /** 子板块3：知识点热力图 — 知识点频次 + 得分率聚合 */
  async getKnowledgeHeatmap(params?: {
    grade?: string;
    subject?: string;
    assignment_ids?: number[];
  }): Promise<KnowledgeHeatmapResponse> {
    const queryParams: Record<string, unknown> = {};
    if (params?.grade) queryParams.grade = params.grade;
    if (params?.subject) queryParams.subject = params.subject;
    // assignment_ids 以数组形式传给 axios，axios 会序列化为 ?assignment_ids=1&assignment_ids=2
    if (params?.assignment_ids?.length) {
      queryParams.assignment_ids = params.assignment_ids;
    }
    const { data } = await api.get("/analytics/knowledge-heatmap", {
      params: queryParams,
      // 使用 URLSearchParams 序列化数组参数为 ?assignment_ids=1&assignment_ids=2 格式，
      // 兼容 FastAPI 的 list[int] Query 解析
      paramsSerializer: (params) => {
        const sp = new URLSearchParams();
        Object.entries(params).forEach(([key, val]) => {
          if (Array.isArray(val)) {
            val.forEach((v) => sp.append(key, String(v)));
          } else if (val !== undefined && val !== null) {
            sp.append(key, String(val));
          }
        });
        return sp.toString();
      },
    });
    return data;
  },
};
