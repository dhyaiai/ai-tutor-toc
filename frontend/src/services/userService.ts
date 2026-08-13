/**
 * 用户管理 API 服务（仅超级管理员可用）
 *
 * 注册功能已取消，账号由超级管理员在"账号设置"中创建：
 * - 手机号必填（登录账号）
 * - 用户名选填（显示名称，不填则显示手机号）
 * - 初始密码由后端随机生成（12 位字母数字），仅返回一次
 * 支持编辑（改手机号/用户名/重置密码/切换角色）与删除（级联清理用户全部数据）。
 * 所有请求通过 api 实例自动携带 JWT token，非管理员访问后端返回 403。
 */

import api from "./api";

/** 用户信息 */
export interface UserInfo {
  id: number;
  phone: string;           // 手机号（登录账号）
  username: string | null; // 用户名（显示名称，可能为空）
  email: string | null;
  /** 用户角色：admin=超级管理员，user=普通用户 */
  role: string;
  created_at: string;
}

/** 创建用户响应（含仅返回一次的初始密码） */
export interface UserCreateResponse extends UserInfo {
  initial_password: string;
}

/** 编辑用户请求体（全部字段可选，不传则不修改） */
export interface UserUpdateData {
  /** 新手机号（登录账号），不传则不修改 */
  phone?: string;
  /** 新用户名（显示名称），不传则不修改。传 null 表示清除自定义名称 */
  username?: string | null;
  /** 新密码，不传则不修改 */
  password?: string;
  /** 目标角色：admin=超级管理员 / user=普通用户 */
  role?: string;
}

export const userService = {
  /** 创建普通用户账号：手机号必填，用户名选填，初始密码由后端随机生成 */
  async createUser(phone: string, username?: string | null): Promise<UserCreateResponse> {
    const { data } = await api.post<UserCreateResponse>("/users", { phone, username: username || null });
    return data;
  },

  /** 用户列表（按创建时间倒序，新用户在前） */
  async listUsers(): Promise<UserInfo[]> {
    const { data } = await api.get<UserInfo[]>("/users");
    return data;
  },

  /** 编辑用户：修改手机号 / 用户名 / 重置密码 / 切换角色 */
  async updateUser(id: number, payload: UserUpdateData): Promise<UserInfo> {
    const { data } = await api.patch<UserInfo>(`/users/${id}`, payload);
    return data;
  },

  /** 删除用户：级联清理该用户全部数据（作业、AI 题目、会话、测评记录等） */
  async deleteUser(id: number): Promise<void> {
    await api.delete(`/users/${id}`);
  },
};
