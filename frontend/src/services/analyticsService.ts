import api from "./api";

export const analyticsService = {
  async getOverview(params?: { grade?: string; subject?: string }) {
    const { data } = await api.get("/analytics/overview", { params });
    return data;
  },

  async getScoreTrend(params?: { grade?: string; subject?: string; semester?: string }) {
    const { data } = await api.get("/analytics/score-trend", { params });
    return data;
  },

  async getWeakness(params?: { grade?: string; subject?: string; semester?: string; limit?: number }) {
    const { data } = await api.get("/analytics/weakness", { params });
    return data;
  },
};
