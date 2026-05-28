/**
 * RTVoice Admin Console API 客户端。
 *
 * 鉴权模型：登录后服务端下发 HttpOnly 会话 cookie，浏览器随同源请求自动带上，
 * 前端不持有任何 secret。因此所有请求都用 credentials:"include"，且因为 UI 与
 * 各服务接口（/v1/*、/auth/*）同源，路径用相对根路径即可，无需注入 API base URL。
 */

const BASE_PATH = "/admin-v2";

export interface ApiError {
  type?: string;
  code?: string;
  message?: string;
  detail?: string;
  error?: string;
}

type ApiFetchInit = RequestInit & {
  params?: Record<string, string | number | boolean | undefined>;
  skipAuthRedirect?: boolean;
};

export async function apiFetch<T>(path: string, init?: ApiFetchInit): Promise<T> {
  const { params, skipAuthRedirect = false, ...fetchInit } = init ?? {};

  let url = path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined) qs.set(k, String(v));
    }
    const q = qs.toString();
    if (q) url += (url.includes("?") ? "&" : "?") + q;
  }

  const headers = new Headers({ "Content-Type": "application/json" });
  const extra = fetchInit.headers;
  if (extra) {
    new Headers(extra as HeadersInit).forEach((v, k) => headers.set(k, v));
  }

  const res = await fetch(url, { ...fetchInit, headers, credentials: "include" });

  // 会话失效 → 回登录页（basePath 感知）
  if (res.status === 401 && !skipAuthRedirect) {
    if (typeof window !== "undefined" && !window.location.pathname.endsWith("/login")) {
      window.location.assign(`${BASE_PATH}/login`);
    }
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = data as ApiError;
    throw new Error(err?.message ?? err?.detail ?? err?.error ?? `HTTP ${res.status}`);
  }
  return data as T;
}
