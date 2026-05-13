# SP11 TLS — handshake internal_error finding（待 SP12 修）

**症状**：Caddy 启动正常 + 自签 cert 三个 SAN (`192.168.66.163` / `127.0.0.1` / `localhost`) 都成功 obtain。
但 host 上 `curl -sk https://...` 全 HTTP 000，openssl s_client 能拿到 cert chain
但 curl TLS 握手中收到 server `TLS alert internal_error (80)`。

**关键证据**：
- caddy 容器内部 `wget -qO- https://localhost/info` **成功** — 返完整 realtime /info JSON
- caddy 容器外（host）curl `--resolve` 强制 SNI=192.168.66.163 仍 000
- `openssl s_client -connect host:443 -servername host < /dev/null` 拿到 cert chain（但没完整握手）
- prometheus alerting rules ✅ 4 groups 5 rules 加载
- Grafana 旧 mount issue 不相关（与 SP11 无关，pre-existing）

**疑似原因**（按可能性排）：
1. docker-proxy v25 在 TLS 1.3 ALPN h2 路径上有 bug
2. Caddy `tls internal` + IP SAN 在 IPv4 单栈 listener 上的 ALPN 协商 bug
3. HTTP/3 listener 与 docker-proxy TCP 路径冲突
4. 客户端 SNI 处理（curl OpenSSL 3.0.13 + IP SAN）

**未做（避免无头排查）**：
- Caddy debug log level 全开看 handshake 细节
- 关 HTTP/3 (`servers { protocols h1 h2 }`)
- 试 `tls internal { issuer internal }` 显式 issuer
- 改用 caddy 内置 `tls /path/cert.pem /path/key.pem` 跳过 internal CA

**impact**：
- TLS 路径**不可用** — Caddy 启动 OK 但任何 host 外 HTTPS 都拿不到 response
- 客户端必须暂用 HTTP（端口 9000/9190/9880/8000）
- 之前 SP10 prod 部署的 HTTP 直访路径仍可用，**没有回归**

**SP12 排查步骤**：
1. caddy `log { level DEBUG }`，重启捕一次 curl 完整 handshake 日志
2. 如确认是 HTTP/3 / ALPN 问题：Caddyfile global `servers { protocols h1 h2 }` 关掉 HTTP/3
3. 如还不通：用 mkcert / openssl 自签 cert 喂给 `tls /pem /key`，跳过 internal CA
4. 一定要测：浏览器导入 root CA 后看是否能用（user-facing 主路径）

**Caddy 仍提供价值**：
- 一处 ingress 路由（即使 HTTP 不通 HTTPS 也用了 Caddy 把请求转发到对应 service —— 已在 docker-compose.tls.yml）
- 此 finding 不阻塞 SP10 已落地的 G3/G4 metrics + securitySchemes

**MANUAL_VALIDATION_QUEUE.md 追加项**：
- 浏览器导入 caddy root CA → https://192.168.66.163 4 tab demo 实测
