/**
 * 会话管理 API 服务
 *
 * 封装会话和消息的后端 API 调用，提供类型安全的接口。
 * 所有请求通过 api 实例自动携带 JWT token 和 401 自动刷新。
 */

import api from "./api";

// ============ 类型定义 ============

/** 会话消息 */
export interface ConversationMessage {
  id: number;
  conversation_id: number;
  role: "user" | "assistant";
  content: string;
  reasoning?: string | null;
  tool_calls?: string[] | null;
  created_at: string;
}

/** 会话摘要（列表展示用，不含完整消息） */
export interface ConversationListItem {
  id: number;
  title: string;
  subject?: string | null;
  status: number;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message?: string | null;
}

/** 会话详情（含完整消息列表） */
export interface ConversationDetail {
  id: number;
  title: string;
  subject?: string | null;
  status: number;
  created_at: string;
  updated_at: string;
  messages: ConversationMessage[];
  message_count: number;
  last_message?: string | null;
}

/** 会话列表响应 */
interface ConversationListResponse {
  items: ConversationListItem[];
  total: number;
}

/** 创建会话请求体 */
interface CreateConversationRequest {
  title?: string;
  subject?: string;
}

/** 更新会话请求体 */
interface UpdateConversationRequest {
  title?: string;
  subject?: string;
}

/** 保存消息请求体 */
interface SaveMessageRequest {
  role: "user" | "assistant";
  content: string;
  reasoning?: string | null;
  tool_calls?: string[] | null;
}

// ============ API 方法 ============

export const conversationService = {
  /**
   * 获取当前用户的会话列表
   * 按更新时间倒序排列，仅返回未删除的会话
   */
  async list(): Promise<ConversationListItem[]> {
    const { data } = await api.get<ConversationListResponse>("/conversations");
    return data.items;
  },

  /**
   * 创建新会话
   * @param title 会话标题（可选，默认"新对话"）
   * @param subject 关联学科（可选）
   */
  async create(title?: string, subject?: string): Promise<ConversationDetail> {
    const body: CreateConversationRequest = {};
    if (title) body.title = title;
    if (subject) body.subject = subject;
    const { data } = await api.post<ConversationDetail>("/conversations", body);
    return data;
  },

  /**
   * 获取会话详情（含完整消息历史）
   * @param id 会话ID
   */
  async get(id: number): Promise<ConversationDetail> {
    const { data } = await api.get<ConversationDetail>(`/conversations/${id}`);
    return data;
  },

  /**
   * 更新会话信息
   * @param id 会话ID
   * @param updates 要更新的字段
   */
  async update(
    id: number,
    updates: UpdateConversationRequest
  ): Promise<ConversationDetail> {
    const { data } = await api.patch<ConversationDetail>(
      `/conversations/${id}`,
      updates
    );
    return data;
  },

  /**
   * 删除会话（软删除，数据保留）
   * @param id 会话ID
   */
  async delete(id: number): Promise<void> {
    await api.delete(`/conversations/${id}`);
  },

  /**
   * 保存单条消息到指定会话
   * @param conversationId 会话ID
   * @param message 消息内容
   */
  async saveMessage(
    conversationId: number,
    message: SaveMessageRequest
  ): Promise<ConversationMessage> {
    const { data } = await api.post<ConversationMessage>(
      `/conversations/${conversationId}/messages`,
      message
    );
    return data;
  },

  /**
   * 批量保存消息到指定会话
   * 用于前端在关闭抽屉时一次性持久化整段对话
   * @param conversationId 会话ID
   * @param messages 消息列表
   */
  async saveMessagesBatch(
    conversationId: number,
    messages: SaveMessageRequest[]
  ): Promise<void> {
    await api.post(
      `/conversations/${conversationId}/messages/batch`,
      messages
    );
  },
};
