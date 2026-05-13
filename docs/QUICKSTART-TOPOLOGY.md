# RTVoice 接入网络拓扑速查

集成 RTVoice 前先确认你的拓扑——这决定 URL / TLS / CORS 怎么配。

## 三种部署形态

### A — 同机 docker network（最简单）

```
[ Your app container ] ── docker network rtvoice_net ──┬─ stt-server:9090
                                                       ├─ tts-server:9880
                                                       ├─ token-server:8000
                                                       └─ realtime-server:9000
```

- **URL**：容器主机名，e.g. `http://stt-server:9090/v1/asr`
- **TLS**：不需要
- **CORS**：不需要
- **典型场景**：你的应用也在同台 GPU 机上 docker compose 跑
- **怎么连**：你的 `docker-compose.yml` 加 `networks: { external: true, name: rtvoice_rtvoice_net }`

### B — LAN 内 + Caddy 自签 TLS（**当前 prod 推荐**）

```
[ Your app on LAN ] ──HTTPS:443──→ Caddy ──docker network──┬─ stt
192.168.66.x        wss:443           rtvoice              ├─ tts
                                                            ├─ token
                                                            └─ realtime
```

- **URL**：`https://192.168.66.163/v1/...` `wss://192.168.66.163/v1/realtime/{sid}`
- **TLS**：必须先信任 Caddy root CA（运行 `scripts/get-rtvoice-ca.sh`）
- **CORS**：如客户端是浏览器，需在 RTVoice prod `.env` 加 `RTVOICE_CORS_ORIGINS=https://your-client.local,https://...`
- **典型场景**：CozyVoice / 三方 web app 跑在 LAN 内不同机器

### C — 公网域名 + Let's Encrypt

```
[ Anywhere ] ──HTTPS:443──→ voice.example.com (Caddy LE) ──→ Caddy ──→ services
```

- **URL**：`https://voice.example.com/v1/...`
- **TLS**：Caddy 自动 Let's Encrypt（修 `Caddyfile` 站点段为 `voice.example.com { ... }` 去掉 `tls internal`）
- **CORS**：必须配 `RTVOICE_CORS_ORIGINS`
- **典型场景**：跨区域 / 多 client / SaaS-like

---

## URL 对照表（按拓扑切片）

| Service | 容器内 (A) | LAN Caddy (B) | 公网域名 (C) |
|---|---|---|---|
| token | `http://token-server:8000/v1/tokens` | `https://192.168.66.163/v1/tokens` | `https://voice.example.com/v1/tokens` |
| stt | `ws://stt-server:9090/v1/asr` | `wss://192.168.66.163/v1/asr` | `wss://voice.example.com/v1/asr` |
| tts | `http://tts-server:9880/v1/tts/stream` | `https://192.168.66.163/v1/tts/stream` | `https://voice.example.com/v1/tts/stream` |
| realtime | `http://realtime-server:9000/v1/sessions` | `https://192.168.66.163/v1/sessions` | `https://voice.example.com/v1/sessions` |
| realtime WS | `ws://realtime-server:9000/v1/realtime/{sid}` | `wss://192.168.66.163/v1/realtime/{sid}` | `wss://voice.example.com/v1/realtime/{sid}` |

---

## 鉴权 / Bearer 三路

RTVoice WS 支持 3 种 Bearer 传递方式（按优先级）：

1. **`Sec-WebSocket-Protocol: bearer.<token>`** —— 浏览器友好（标准做法）；服务器会按 RFC 6455 echo 同字面
2. **`Authorization: Bearer <token>`** —— server-to-server 首选
3. **`?token=<token>`** —— query param fallback（URL log 有泄漏风险）

HTTP endpoints 只接 #2。

## Scopes 速查

| Scope | 服务 | endpoints |
|---|---|---|
| `tokens` | token-server | POST /v1/tokens |
| `stt` | stt-server | WS /v1/asr |
| `tts` | tts-server | GET/POST /v1/voices, POST /v1/tts/stream, WS /v1/tts/stream_ws |
| `realtime` | realtime-server | POST/DELETE /v1/sessions*, WS /v1/realtime/{id} |

key 创建时 scopes 列表不全 → 调那条 endpoint 返 403 `auth.scope_denied`。

## CORS 配置

环境变量 `RTVOICE_CORS_ORIGINS`（4 service 共享）：

| 值 | 含义 |
|---|---|
| `*` | 允许任意 origin（默认；**生产建议改具体值**） |
| `https://app1.local,https://app2.local` | 多个明确 origin |

浏览器报 CORS preflight 失败时，先看 `docker logs rtvoice-realtime --tail 5` 是否有 CORS error。

## 常见雷区

- **WS subprotocol 不 echo** → 浏览器 close 1006 → SP9 T1 已修，但确保 RTVoice 在 v0.15.0+
- **Caddy root CA 没信任** → curl/Python `verify=...` 报 unable to get local issuer → 走 `scripts/get-rtvoice-ca.sh`
- **容器主机名外不可解析** → 拓扑 A 内部用没问题，外部用 → 走拓扑 B/C
- **`ws_url` 返容器主机名** → v0.15.0+ 已读 X-Forwarded-Host / Host header；如还是返容器名说明 prod 还没升级
