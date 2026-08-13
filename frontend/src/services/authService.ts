import api from "./api";
import { clearSession } from "./authStorage";

export interface LoginParams {
  phone: string;
  password: string;
}

/** 当前登录用户信息（GET /users/me 返回） */
export interface MeResponse {
  id: number;
  phone: string;
  username: string | null;
  role: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user_id: number;
  phone: string;           // 手机号（登录账号）
  username: string | null; // 显示名称（可选，无则前端显示手机号）
  /** 用户角色：admin=超级管理员，user=普通用户（前端据此控制"账号设置"入口） */
  role: string;
}

export const authService = {
  async login(params: LoginParams): Promise<TokenResponse> {
    const { data } = await api.post("/auth/login", params);
    return data;
  },

  /** 获取当前登录用户信息（页面刷新后恢复登录态） */
  async getMe(): Promise<MeResponse> {
    const { data } = await api.get("/users/me");
    return data;
  },

  logout() {
    clearSession();
    // 清除 DEV 模式下登录/刷新时种下的 access_token cookie
    // （/api/v1/files/ 私有文件鉴权用，<img> 无法带 Authorization 头只能靠 cookie）。
    // 不设置则登出后旧凭证残留，换账号登录前文件仍可访问
    document.cookie = "access_token=; Max-Age=0; path=/";
  },
};
