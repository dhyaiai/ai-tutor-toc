/**
 * Token 刷新单一来源（Single Source of Truth）。
 *
 * api.ts（axios 拦截器）和 authedFetch.ts（原生 fetch）共用此模块，
 * 确保并发 401 只触发一次 /auth/refresh 请求。
 * 后端 refresh token 单次使用（JTI 黑名单），并发刷新会导致互相踢下线。
 *
 * 跨标签页同步：通过 localStorage storage 事件监听其他标签页的刷新结果，
 * 避免多个标签页同时刷新导致互相踢下线。
 */
import {
  getRefreshToken,
  setSession,
  clearSession,
  getAccessToken,
  AUTH_KEYS,
} from "./authStorage";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api/v1";

// 全局刷新互斥锁：整个应用只有一个刷新 Promise，所有调用方共享
let _refreshPromise: Promise<string | null> | null = null;

/**
 * 监听其他标签页刷新 token 的结果。
 * 当其他标签页成功刷新时，自动更新本地 session，避免重复刷新。
 */
function setupCrossTabSync(): void {
  if (typeof window === "undefined" || !window.addEventListener) return;

  window.addEventListener("storage", (event) => {
    // 其他标签页主动登出（清空 access token）：本标签页也同步登出，
    // 避免残留已失效的登录态导致误以为还在登录
    if (event.key === AUTH_KEYS.ACCESS_TOKEN && !event.newValue) {
      clearSession();
      return;
    }
    const isTokenKey =
      event.key === AUTH_KEYS.ACCESS_TOKEN || event.key === AUTH_KEYS.REFRESH_TOKEN;
    if (!isTokenKey || !event.newValue) return;
    // 其他标签页刷新成功：同步本地 session。
    // 注意 refresh_token 单次使用（JTI 黑名单），必须与 access_token 一并同步，
    // 否则本标签页下次刷新会用已被作废的旧 refresh_token 而被 401 踢下线。
    // storage 事件在 localStorage 更新后才触发，此处读取到的都是最新值。
    const accessToken = getAccessToken();
    const refreshToken = getRefreshToken();
    if (accessToken) {
      setSession({
        access_token: accessToken,
        refresh_token: refreshToken ?? undefined,
      });
    }
  });
}

// 初始化跨标签页同步
setupCrossTabSync();

/**
 * 等待另一个标签页通过 storage 事件推送新 token（跨标签页刷新同步）。
 * 用于修复"两个标签页同时持过期 token，同时用单次使用 refresh token 刷新，
 * 后到者被 JTI 黑名单 401 拒绝而被强制登出"的并发互踢问题：
 * 若 401 是因为另一 tab 已刷新成功（其 storage 事件马上推送新 token），
 * 本 tab 只需拿到新 token 继续用即可，无需登出。
 */
function waitForCrossTabSync(timeoutMs = 1500): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      cleanup();
      resolve(false);
    }, timeoutMs);
    const handler = (event: StorageEvent) => {
      if (event.key === AUTH_KEYS.ACCESS_TOKEN && event.newValue) {
        settled = true;
        cleanup();
        resolve(true);
      }
    };
    const cleanup = () => {
      if (settled) return;
      clearTimeout(timer);
      window.removeEventListener("storage", handler);
    };
    window.addEventListener("storage", handler);
  });
}

/**
 * 刷新一次 access token。
 * 并发调用返回同一个 Promise，避免重复刷新导致 refresh token 失效。
 *
 * @returns 新 access token；失败返回 null（refresh token 失效时会清会话跳登录）
 */
export function refreshAccessTokenOnce(): Promise<string | null> {
  if (!_refreshPromise) {
    _refreshPromise = _doRefresh();
  }
  return _refreshPromise;
}

async function _doRefresh(): Promise<string | null> {
  try {
    const refreshToken = getRefreshToken();
    if (!refreshToken) return null;
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) {
      if (res.status === 401) {
        // 401 通常是 refresh token 已被并发消费：单次使用机制（JTI 黑名单）下，
        // 两个标签页同时刷新时只有第一个成功，第二个必然 401。
        // 短暂等待跨标签页同步：若另一 tab 刷新成功已推送新 token，直接用即可，
        // 不要因此把本 tab 强制登出（否则用户会被莫名踢下线）。
        await waitForCrossTabSync();
        const syncedAccess = getAccessToken();
        if (syncedAccess) {
          return syncedAccess;
        }
        clearSession();
        const redirect = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = `/login?redirect=${redirect}`;
      }
      return null;
    }
    const data = await res.json();
    setSession({
      access_token: data.access_token,
      refresh_token: data.refresh_token,
      role: data.role ?? undefined,
      phone: data.phone ?? undefined,
      username: data.username ?? undefined,
    });
    return data.access_token as string;
  } catch {
    // 网络故障/5xx：保留会话，等待下次重试
    return null;
  } finally {
    _refreshPromise = null;
  }
}
