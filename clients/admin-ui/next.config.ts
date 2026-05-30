import type { NextConfig } from "next";

// RTVoice Admin Console。独立 Node 容器（output:'standalone'，照搬 CozyMemory 做法）。
// 通过 Caddy 挂在 /admin/ 下；basePath 让所有路由与静态资源在该前缀下正确解析。
// 与各服务接口（/v1/*、/auth/*）同源，因此 API 调用用相对路径、无需运行时注入 URL。
const nextConfig: NextConfig = {
  output: "standalone",
  basePath: "/admin",
};

export default nextConfig;
