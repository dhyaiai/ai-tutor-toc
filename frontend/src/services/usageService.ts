/**
 * 数据看板 API 服务
 *
 * 封装 LLM Token 用量统计查询：按日聚合的调用次数与 Token 消耗，
 * 以及区间汇总指标（日均 Token 消耗量 / 日均调用次数）。
 * 所有请求通过 api 实例自动携带 JWT token。
 */

import api from "./api";

/** 单日用量聚合 */
export interface DailyUsageItem {
  date: string; // YYYY-MM-DD
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

/** 区间汇总指标 */
export interface UsageSummary {
  days: number;
  total_calls: number;
  total_tokens: number;
  avg_daily_calls: number;
  avg_daily_tokens: number;
}

export interface TokenUsageResponse {
  summary: UsageSummary;
  daily: DailyUsageItem[];
}

export const usageService = {
  /** 获取最近 N 天的 Token 用量统计 */
  async getTokenUsage(days: number): Promise<TokenUsageResponse> {
    const { data } = await api.get<TokenUsageResponse>("/dashboard/token-usage", {
      params: { days },
    });
    return data;
  },
};
