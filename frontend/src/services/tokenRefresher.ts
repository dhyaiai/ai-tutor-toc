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
    // 其他标签页刷新成功：通过共享键推送了新 refresh_token。
    // 必须用 event.newValue（另一标签页写入的值），不能用 getRefreshToken()——
    // refresh_token 存 sessionStorage（每标签页隔离），本页读到的仍是旧值；
    // 且 refresh_token 单次使用（JTI 黑名单），若不同步新 token，
    // 本标签页下次刷新会用已作废的旧 token 被 401 强制登出。
    if (event.key === AUTH_KEYS.SHARED_REFRESH_TOKEN && event.newValue) {
      sessionStorage.setItem(AUTH_KEYS.REFRESH_TOKEN, event.newValue);
      return;
    }
    // 其他标签页登出：清掉本页 refresh_token（ACCESS_TOKEN 的 storage 事件
    // 会先触发 clearSession，此处兜底处理共享键单独被清除的场景）
    if (event.key === AUTH_KEYS.SHARED_REFRESH_TOKEN && !event.newValue) {
      sessionStorage.removeItem(AUTH_KEYS.REFRESH_TOKEN);
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
  // 记录刷新前的 access token：401 分支需要区分"跨标签页同步成功（token 变了）"
  // 与"刷新真失败（token 未变）"——access_token 存 localStorage 永远有值，
  // 仅凭非空会把本地已过期的旧 token 当刷新成功返回，导致假登录死循环
  const beforeAccess = getAccessToken();
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
        const synced = await waitForCrossTabSync();
        const afterAccess = getAccessToken();
        // 必须同时满足"确实收到另一标签页的 storage 事件"且"token 真的变了"：
        // 单标签页下 refresh token 真失效（过期/被撤销）时，等 1.5s 超时后
        // token 未变化，此时应清会话跳登录，而不是把旧 token 当成功返回
        if (synced && afterAccess && afterAccess !== beforeAccess) {
          return afterAccess;
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
