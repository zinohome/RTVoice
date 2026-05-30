/**
 * RTVoice Admin Console API 客户端。
 *
 * 鉴权模型：登录后服务端下发 HttpOnly 会话 cookie，浏览器随同源请求自动带上，
 * 前端不持有任何 secret。因此所有请求都用 credentials:"include"，且因为 UI 与
 * 各服务接口（/v1/*、/auth/*）同源，路径用相对根路径即可，无需注入 API base URL。
 */

export const BASE_PATH = "/admin";

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

async function errMessage(res: Response): Promise<string> {
  const data = (await res.json().catch(() => ({}))) as ApiError;
  return data?.message ?? data?.detail ?? data?.error ?? `HTTP ${res.status}`;
}

/** 取二进制响应（如 TTS 返回的 WAV）。失败抛 Error。 */
export async function apiBlob(path: string, init?: RequestInit): Promise<Blob> {
  const res = await fetch(path, { ...init, credentials: "include" });
  if (!res.ok) throw new Error(await errMessage(res));
  return res.blob();
}

/** multipart/form-data 上传（如音色注册）。返回解析后的 JSON。 */
export async function apiUpload<T>(path: string, form: FormData, method = "POST"): Promise<T> {
  const res = await fetch(path, { method, body: form, credentials: "include" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = data as ApiError;
    throw new Error(err?.message ?? err?.detail ?? err?.error ?? `HTTP ${res.status}`);
  }
  return data as T;
}

/** 把相对路径转成同源 WebSocket URL（ws/wss 跟随当前协议）。 */
export function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}
