"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Method = "GET" | "POST" | "DELETE" | "WS";

interface Endpoint {
  method: Method;
  path: string;
  auth: string;
  description: string;
}

interface Service {
  id: string;
  label: string;
  description: string;
  baseUrl: string;
  endpoints: Endpoint[];
}

const METHOD_VARIANTS: Record<Method, string> = {
  GET: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400",
  POST: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400",
  DELETE: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400",
  WS: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400",
};

const SERVICES: Service[] = [
  {
    id: "stt",
    label: "STT 转写",
    description: "实时流式语音识别 — sherpa-onnx Streaming Zipformer 中英文",
    baseUrl: "http://stt-server:9090",
    endpoints: [
      { method: "WS", path: "/v1/asr", auth: "Bearer", description: "流式语音识别（PCM int16 LE 16kHz mono）" },
      { method: "POST", path: "/v1/transcribe", auth: "Bearer", description: "单文件转写（multipart/form-data）" },
      { method: "GET", path: "/health", auth: "无", description: "服务健康检查" },
      { method: "GET", path: "/info", auth: "无", description: "服务版本与配置信息" },
    ],
  },
  {
    id: "tts",
    label: "TTS 合成",
    description: "流式语音合成 + 音色克隆 — Fun-CosyVoice 3 (0.5B GPU)",
    baseUrl: "http://tts-server:9880",
    endpoints: [
      { method: "POST", path: "/v1/tts/stream", auth: "Bearer", description: "HTTP 单次流式合成，返回分块 PCM" },
      { method: "WS", path: "/v1/tts/stream_ws", auth: "Bearer", description: "双向流式合成，客户端流式发文本，服务端流式返 PCM" },
      { method: "GET", path: "/v1/voices", auth: "Bearer", description: "列出所有可用音色" },
      { method: "POST", path: "/v1/voices", auth: "TTS_ADMIN_API_KEY", description: "注册新音色（音色克隆，需管理员 Key）" },
      { method: "DELETE", path: "/v1/voices/{spk_id}", auth: "TTS_ADMIN_API_KEY", description: "删除音色（需管理员 Key）" },
      { method: "GET", path: "/health", auth: "无", description: "服务健康检查" },
      { method: "GET", path: "/info", auth: "无", description: "服务版本与模型信息" },
    ],
  },
  {
    id: "realtime",
    label: "Realtime 对话",
    description: "实时语音对话网关 — OpenAI Realtime API 风格，支持 LiveKit 高级模式",
    baseUrl: "http://realtime-server:9000",
    endpoints: [
      { method: "POST", path: "/v1/sessions", auth: "Bearer", description: "创建对话 session，返回 session_id 与 ws_url" },
      { method: "WS", path: "/v1/realtime/{session_id}", auth: "session_id", description: "双向音频/事件流（PCM + JSON 事件）" },
      { method: "POST", path: "/v1/tokens", auth: "APP_API_KEY", description: "签发 LiveKit room JWT（高级模式）" },
      { method: "GET", path: "/health", auth: "无", description: "服务健康检查" },
      { method: "GET", path: "/info", auth: "无", description: "服务版本信息" },
    ],
  },
  {
    id: "admin",
    label: "Admin 管理",
    description: "API Key 生命周期管理 — 需要 admin scope 鉴权",
    baseUrl: "http://gateway",
    endpoints: [
      { method: "POST", path: "/v1/auth/login", auth: "用户名/密码", description: "管理员登录，设置 HttpOnly 会话 Cookie" },
      { method: "POST", path: "/v1/auth/logout", auth: "Cookie", description: "退出登录，清除会话" },
      { method: "GET", path: "/v1/admin/keys", auth: "Bearer / Cookie", description: "列出所有 API Key（不含 secret）" },
      { method: "POST", path: "/v1/admin/keys", auth: "Bearer / Cookie", description: "创建新 Key（secret 仅在此响应中展示一次）" },
      { method: "GET", path: "/v1/admin/keys/{key_id}", auth: "Bearer / Cookie", description: "查询单个 Key 详情" },
      { method: "POST", path: "/v1/admin/keys/{key_id}/revoke", auth: "Bearer / Cookie", description: "吊销 Key（幂等）" },
      { method: "POST", path: "/v1/admin/keys/{key_id}/rotate", auth: "Bearer / Cookie", description: "轮转 Key Secret（旧 secret 立即失效）" },
    ],
  },
];

function MethodBadge({ method }: { method: Method }) {
  return (
    <span
      className={`inline-block shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${METHOD_VARIANTS[method]}`}
    >
      {method}
    </span>
  );
}

function EndpointsTable({ endpoints }: { endpoints: Endpoint[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-xs text-muted-foreground">
            <th className="pb-2 pr-4 text-left font-medium">方法</th>
            <th className="pb-2 pr-4 text-left font-medium">路径</th>
            <th className="pb-2 pr-4 text-left font-medium">鉴权</th>
            <th className="pb-2 text-left font-medium">说明</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {endpoints.map((ep) => (
            <tr key={`${ep.method}-${ep.path}`} className="hover:bg-accent/30">
              <td className="py-2.5 pr-4">
                <MethodBadge method={ep.method} />
              </td>
              <td className="py-2.5 pr-4">
                <code className="text-xs text-primary">{ep.path}</code>
              </td>
              <td className="py-2.5 pr-4 text-xs text-muted-foreground">{ep.auth}</td>
              <td className="py-2.5 text-xs">{ep.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ApiPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">API 接口文档</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          RTVoice 各服务接口速查 · 鉴权统一使用{" "}
          <code className="text-xs">Authorization: Bearer &lt;RTVOICE_API_KEY&gt;</code>
        </p>
      </div>

      <Card className="border-dashed">
        <CardContent className="pt-4 pb-3">
          <div className="flex flex-wrap gap-3 text-xs">
            <span className="flex items-center gap-1.5">
              <MethodBadge method="GET" /> 查询
            </span>
            <span className="flex items-center gap-1.5">
              <MethodBadge method="POST" /> 创建 / 操作
            </span>
            <span className="flex items-center gap-1.5">
              <MethodBadge method="DELETE" /> 删除
            </span>
            <span className="flex items-center gap-1.5">
              <MethodBadge method="WS" /> WebSocket 长连接
            </span>
            <span className="ml-auto text-muted-foreground">
              完整文档见{" "}
              <a
                href="https://github.com/zinohome/RTVoice/tree/main/docs/api"
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2 hover:text-foreground"
              >
                docs/api/
              </a>
            </span>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="stt">
        <TabsList className="w-full justify-start">
          {SERVICES.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>
              {s.label}
            </TabsTrigger>
          ))}
        </TabsList>
        {SERVICES.map((s) => (
          <TabsContent key={s.id} value={s.id} className="mt-4">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">{s.label}</CardTitle>
                <CardDescription>{s.description}</CardDescription>
                <code className="text-xs text-muted-foreground">{s.baseUrl}</code>
              </CardHeader>
              <CardContent>
                <EndpointsTable endpoints={s.endpoints} />
              </CardContent>
            </Card>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
