/**
 * 登录态持久化：所有存储 key 的唯一定义点。
 *
 * 安全设计：
 * - access_token 存 localStorage（短期有效 30 分钟，XSS 泄露窗口有限）
 * - refresh_token 存 sessionStorage（关闭标签页即失效，降低持久化泄露风险）
 * - 全站需配置 CSP 头防止 XSS（script-src 限制）
 *
 * 其它模块一律通过这里的 getter/setter 读写，禁止直接拼字符串 key，
 * 避免 key 拼写漂移，以及"清理会话"时漏删某个 key 导致登录态残留。
 */

/** 会话相关 key 集合（clearSession 按此清单全量清理） */
export const AUTH_KEYS = {
  ACCESS_TOKEN: "access_token",
  REFRESH_TOKEN: "refresh_token",
  USER_ID: "user_id",
  PHONE: "phone",
  USERNAME: "username",
  ROLE: "role",
} as const;

/** 读取 access_token（可能为 null） */
export function getAccessToken(): string | null {
  return localStorage.getItem(AUTH_KEYS.ACCESS_TOKEN);
}

/** 读取 refresh_token（可能为 null） */
export function getRefreshToken(): string | null {
  // refresh_token 存 sessionStorage，关闭标签页即失效
  return sessionStorage.getItem(AUTH_KEYS.REFRESH_TOKEN);
}

/** 读取本地缓存的用户角色（可能为 null） */
export function getStoredRole(): string | null {
  return localStorage.getItem(AUTH_KEYS.ROLE);
}

/** 读取本地缓存的手机号（可能为 null） */
export function getStoredPhone(): string | null {
  return localStorage.getItem(AUTH_KEYS.PHONE);
}

/**
 * 保存登录/刷新接口返回的会话数据。
 * 可选字段缺省时保留旧值（刷新接口只回传部分字段时的兼容行为）。
 */
export function setSession(session: {
  access_token: string;
  refresh_token?: string;
  role?: string | null;
  phone?: string | null;
  username?: string | null;
}): void {
  localStorage.setItem(AUTH_KEYS.ACCESS_TOKEN, session.access_token);
  if (session.refresh_token !== undefined) {
    // refresh_token 存 sessionStorage，关闭标签页即失效（降低 XSS 持久化风险）
    sessionStorage.setItem(AUTH_KEYS.REFRESH_TOKEN, session.refresh_token);
  }
  if (session.role !== undefined) {
    localStorage.setItem(AUTH_KEYS.ROLE, session.role ?? getStoredRole() ?? "user");
  }
  if (session.phone !== undefined) {
    localStorage.setItem(AUTH_KEYS.PHONE, session.phone ?? getStoredPhone() ?? "");
  }
  if (session.username !== undefined) {
    // username 可能为 null，存空字符串（localStorage 不存 null）
    localStorage.setItem(AUTH_KEYS.USERNAME, session.username ?? "");
  }
}

/** 同步 /users/me 返回的真实用户资料到本地快照 */
export function setUserProfile(me: {
  id: number;
  phone: string;
  username: string | null;
  role: string;
}): void {
  localStorage.setItem(AUTH_KEYS.USER_ID, String(me.id));
  localStorage.setItem(AUTH_KEYS.PHONE, me.phone);
  localStorage.setItem(AUTH_KEYS.USERNAME, me.username || "");
  localStorage.setItem(AUTH_KEYS.ROLE, me.role);
}

/** 写入登录接口返回的 user_id */
export function setStoredUserId(id: string | number): void {
  localStorage.setItem(AUTH_KEYS.USER_ID, String(id));
}

/** 清除全部登录态（登出 / 会话失效时调用） */
export function clearSession(): void {
  // 清理 localStorage 中的会话数据
  Object.values(AUTH_KEYS).forEach((key) => localStorage.removeItem(key));
  // 清理 sessionStorage 中的 refresh_token
  sessionStorage.removeItem(AUTH_KEYS.REFRESH_TOKEN);
}
