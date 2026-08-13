/**
 * 带登录凭证的原生 fetch 工具。
 *
 * 用于无法走 axios 拦截器的场景：音频/PDF blob 下载、SSE 流等
 * （原生 fetch 不会自动附加 Authorization 头，也不会在 401 时刷新 token）。
 * 这里统一封装：附加 Bearer token + 401 自动刷新重放，与 api.ts 拦截器行为对齐。
 *
 * Token 刷新逻辑已收敛到 services/tokenRefresher.ts，与 api.ts 共用同一个刷新锁，
 * 避免并发刷新导致 refresh token 单次使用校验失败（互相踢下线）。
 */
import { getAccessToken } from "../services/authStorage";
import { refreshAccessTokenOnce } from "../services/tokenRefresher";

/**
 * 带 Bearer token 的原生 fetch：401 时自动刷新 token 并重放一次。
 *
 * 调用方仍需自行处理返回的响应（如检查 res.ok、转 blob）。
 * 注意：请求体（body）只能在 fetch 中读取一次，重放时需传原始 body
 * （字符串/FormData 均可复用）。
 */
export async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers || {});
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response = await fetch(url, { ...init, headers });
  if (response.status === 401) {
    const newToken = await refreshAccessTokenOnce();
    if (newToken) {
      headers.set("Authorization", `Bearer ${newToken}`);
      response = await fetch(url, { ...init, headers });
    }
  }
  return response;
}
