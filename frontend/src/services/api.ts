import axios from "axios";
import { getAccessToken } from "./authStorage";
import { refreshAccessTokenOnce } from "./tokenRefresher";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";

const api = axios.create({
  baseURL: API_BASE,
  timeout: 120000,
  // 不设默认 Content-Type，让 axios 根据数据类型自动判断：
  // plain object → application/json，FormData → multipart/form-data（浏览器自动加 boundary）
});

// Request interceptor: attach access token
api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor: auto refresh on 401
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    // 登录接口的 401 是正常的认证失败（账号或密码错误），
    // 不应触发 token 刷新或页面跳转，直接透传错误给登录页显示
    if (originalRequest.url?.includes('/auth/login')) {
      return Promise.reject(error);
    }

    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;

      // 使用共享刷新模块（与 authedFetch 共用同一个刷新锁，避免并发刷新互相踢下线）
      const newToken = await refreshAccessTokenOnce();

      if (newToken) {
        originalRequest.headers.Authorization = `Bearer ${newToken}`;
        return api(originalRequest);
      }

      // 刷新失败已被 tokenRefresher 处理（清会话 + 跳登录）
      return Promise.reject(error);
    }

    return Promise.reject(error);
  }
);

export default api;
