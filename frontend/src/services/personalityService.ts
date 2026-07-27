/**
 * 助教性格配置 API 服务
 *
 * 封装自定义微调配置（性格类型/说话风格/评分严格度）的查询与更新操作。
 * 配置对系统内所有 AI 批改统一生效。
 * 所有请求通过 api 实例自动携带 JWT token。
 */

import api from "./api";

/** 用户配置 */
export interface PersonalityConfig {
  id: number;
  user_id: number;
  personality_type: string;
  speaking_style: string;
  strict_level: number;
  update_time?: string | null;
}

export const personalityService = {
  /** 获取当前用户配置（无配置时返回默认值） */
  async get(): Promise<PersonalityConfig> {
    const { data } = await api.get<PersonalityConfig>("/personality");
    return data;
  },

  /** 更新配置 */
  async update(
    updates: Partial<Omit<PersonalityConfig, "id" | "user_id" | "update_time">>
  ): Promise<PersonalityConfig> {
    const { data } = await api.put<PersonalityConfig>("/personality", updates);
    return data;
  },
};
