import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { authService, type TokenResponse } from "../services/authService";
import {
  getAccessToken,
  setSession,
  setStoredUserId,
  setUserProfile,
  clearSession,
} from "../services/authStorage";

interface UserInfo {
  id: number;
  phone: string;            // 手机号（登录账号）
  username: string | null;  // 显示名称（可选，无则显示手机号）
  /** 用户角色：admin=超级管理员，user=普通用户 */
  role: string;
}

interface AuthState {
  user: UserInfo | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (phone: string, password: string) => Promise<void>;
  logout: () => void;
}

interface AuthProviderProps {
  children: ReactNode;
  /** 登录态切换（login/logout）时清空该实例的查询缓存，防止跨账号读到旧账号数据 */
  queryClient?: QueryClient;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children, queryClient }: AuthProviderProps) {
  const [user, setUser] = useState<UserInfo | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Initialize from stored tokens：刷新页面后调 /users/me 拉取真实用户信息。
  // 不再用 localStorage 里的旧快照或伪造 {id:0} 用户——快照可能过期（角色变更/账号删除），
  // 伪造用户会让页面以为已登录但所有请求实际 401；token 无效时直接清空登录态。
  // 增加重试机制：网络抖动/后端重启时最多重试 3 次，指数退避，避免白屏闪烁。
  useEffect(() => {
    const accessToken = getAccessToken();
    if (!accessToken) {
      setIsLoading(false);
      return;
    }

    let cancelled = false;
    const maxRetries = 3;
    const baseDelay = 500; // ms

    const attemptFetch = async (retryCount: number) => {
      if (cancelled) return;
      try {
        const me = await authService.getMe();
        if (cancelled) return;
        setUser({
          id: me.id,
          phone: me.phone,
          username: me.username,
          role: me.role,
        });
        setUserProfile(me);
      } catch (err: any) {
        if (cancelled) return;
        const status = err?.response?.status;
        // 仅 401 才清会话；网络错误/5xx 重试
        if (status === 401) {
          clearSession();
          setUser(null);
          return;
        }
        if (retryCount < maxRetries && (status === undefined || status >= 500 || status === 0)) {
          // 指数退避：500ms, 1000ms, 2000ms
          const delay = baseDelay * Math.pow(2, retryCount);
          await new Promise((r) => setTimeout(r, delay));
          if (!cancelled) {
            await attemptFetch(retryCount + 1);
          }
        } else {
          // 达到最大重试次数或非可重试错误，保留会话待下次机会
          console.warn("[Auth] getMe failed after retries, keeping session:", err);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    attemptFetch(0);
    return () => { cancelled = true; };
  }, []);

  const login = useCallback(async (phone: string, password: string) => {
    // 切换账号前清空旧账号的查询缓存：assignment/analytics 等查询 key 相同，
    // 不清空的话新账号在 30s staleTime 窗口内会直接读到上一账号的数据（数据泄漏）
    queryClient?.clear();
    const result: TokenResponse = await authService.login({ phone, password });
    setSession({
      access_token: result.access_token,
      refresh_token: result.refresh_token,
      role: result.role,
      phone: result.phone,
      username: result.username,
    });
    setStoredUserId(result.user_id);
    setUser({
      id: result.user_id,
      phone: result.phone,
      username: result.username,
      role: result.role,
    });
  }, [queryClient]);

  const logout = useCallback(() => {
    authService.logout();
    clearSession();
    setUser(null);
    // 清空查询缓存，防止登出后/换账号时读到旧账号的敏感数据
    queryClient?.clear();
  }, [queryClient]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        login,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
